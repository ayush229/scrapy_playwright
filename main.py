# main.py
from flask import Flask, request, jsonify, make_response
from functools import wraps
from scraper import scrape_website, crawl_website
import logging
import os
from together import Together
from urllib.parse import urlparse, urljoin
import uuid
import re
import traceback
import json
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["https://agent-ai-production-b4d6.up.railway.app","https://agent-ai-production-b4d6.up.railway.app/agent"], supports_credentials=True, methods=["GET", "POST", "OPTIONS", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"])

# --- Configuration ---
AUTH_USERNAME = "ayush1"
AUTH_PASSWORD = "blackbox098"
SCRAPED_DATA_DIR = "scraped_content"
os.makedirs(SCRAPED_DATA_DIR, exist_ok=True)

# --- Initialize Clients and Logging ---
# Initialize Together client lazily within functions to avoid pickling issues
_together_client = None # Declare a global variable to hold the client instance

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def check_auth(username, password):
    """Checks if provided username and password are valid."""
    return username == AUTH_USERNAME and password == AUTH_PASSWORD

def authenticate():
    """Sends a 401 response that enables basic auth."""
    logger.warning("Authentication failed.")
    return make_response(
        jsonify({"error": "Authentication required"}),
        401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'}
    )

def requires_auth(f):
    """Decorator to protect routes with basic authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == 'OPTIONS':
            return '', 200

        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()

        return f(*args, **kwargs)
    return decorated

def ask_llama(prompt):
    """Sends a prompt to the Together AI Llama model and returns the response."""
    global _together_client # Indicate that we are modifying the global variable
    if _together_client is None:
        try:
            _together_client = Together()
        except Exception as e:
            error_message = f"FATAL: Could not initialize Together client. Ensure TOGETHER_API_KEY is set. Error: {e}"
            logger.error(error_message)
            print(error_message)
            return None
    try:
        response = _together_client.chat.completions.create(
            model="meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
            messages=[{"role": "user", "content": prompt}]
        )
        if response.choices:
            return response.choices[0].message.content.strip()
        else:
            logger.warning("LLM response did not contain choices.")
            return None
    except Exception as e:
        error_message = f"LLM error: {e}\n{traceback.format_exc()}"
        logger.error(error_message)
        print(error_message)
        return None

def get_stored_content(unique_code):
    """
    Retrieves the entire JSON object stored in the file associated with the unique_code.
    Returns the loaded JSON object or None if not found or invalid.
    """
    filepath = os.path.join(SCRAPED_DATA_DIR, f"{unique_code}.txt")
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            error_message = f"Error decoding JSON from {filepath}: {e}"
            logger.error(error_message)
            print(error_message)
            return None
        except Exception as e:
            error_message = f"Error reading file {filepath}: {e}"
            logger.error(error_message)
            print(error_message)
            return None
    return None

def find_relevant_content(content_array, query):
    """
    Finds content objects that are relevant to the query based on non-stop words.
    Args:
        content_array: A list of content objects (structure: {"url": ..., "content": [{"heading":..., "paragraphs":...}]}).
        query:         The user's query string.
    Returns:
        A tuple:
        - list: Relevant content objects.
        - bool: True if a meaningful (non-stop word) match was found, False otherwise.
    """
    stop_words = set([
        "a", "an", "the", "and", "or", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "can", "could", "will", "would",
        "shall", "should", "may", "might", "must", "it's", "don't", "i'm", "you're",
        "he's", "she's", "we're", "they're", "isn't", "aren't", "wasn't", "weren't",
        "haven't", "hasn't", "hadn't", "don't", "doesn't", "didn't", "can't", "couldn't",
        "won't", "wouldn't", "shan't", "shouldn't", "mayn't", "mightn't", "mustn't",
        "you", "i", "he", "she", "it", "we", "they", "this", "that", "these", "those",
        "my", "your", "his", "her", "its", "our", "their", "here", "there", "what",
        "where", "when", "why", "how", "who", "whom", "whose", "with", "without",
        "to", "from", "up", "down", "in", "out", "on", "off", "over", "under", "again",
        "further", "then", "once", "here", "there", "when", "where", "why", "how",
        "all", "any", "both", "each", "few", "many", "more", "most", "some", "such",
        "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "s",
        "t", "m", "d", "ll", "re", "ve", "y",
    ])
    # Extract meaningful query tokens (non-stop words)
    query_tokens = [w.lower() for w in re.findall(r"\b\w+\b", query) if w.isalnum()]
    meaningful_query_tokens = {token for token in query_tokens if token not in stop_words}

    relevant_content = []
    meaningful_match_found = False # Track if any non-stop word match occurred

    if not content_array or not query_tokens: # If no content or no query words
        return [], False

    for content_obj in content_array:
        if not isinstance(content_obj, dict) or 'content' not in content_obj:
            logger.warning(f"Skipping invalid content object format: {content_obj}")
            continue

        page_is_relevant = False
        # Combine all text for the current page for easier searching
        page_text = ""
        for section in content_obj.get('content', []):
            heading = section.get('heading', '') or "" # Ensure heading is a string
            paragraphs = section.get('paragraphs', []) or [] # Ensure paragraphs is a list
            page_text += f" {heading} " + " ".join(paragraphs)

        page_text_lower = page_text.lower()

        # Check if any meaningful query token is in the page text
        for token in meaningful_query_tokens:
            # Use word boundaries for more precise matching
            if re.search(r'\b' + re.escape(token) + r'\b', page_text_lower):
                page_is_relevant = True
                meaningful_match_found = True
                break # Found a meaningful match for this page

        if page_is_relevant:
            relevant_content.append(content_obj)

    return relevant_content, meaningful_match_found


def find_relevant_sentences(text_content, query):
    """
    Placeholder function: Finds sentences in the text relevant to the query.
    Replace this with your actual sentence relevance logic.
    """
    logger.warning("Using placeholder `find_relevant_sentences`. Replace with actual implementation.")
    if not query or not text_content:
        return []

    # Simple placeholder: return sentences containing any non-stop word from the query
    stop_words = set(["a", "an", "the", "is", "in", "it", "of", "and", "to"]) # simplified list
    meaningful_query_words = [w.lower() for w in re.findall(r"\b\w+\b", query) if w.lower() not in stop_words]

    if not meaningful_query_words:
        return [] # Query only contains stop words

    sentences = re.split(r'(?<=[.!?])\s+', text_content) # Basic sentence splitting
    relevant = []
    for sentence in sentences:
        sent_lower = sentence.lower()
        for word in meaningful_query_words:
            if re.search(r'\b' + re.escape(word) + r'\b', sent_lower):
                relevant.append(sentence.strip())
                break
    # Limit context size if needed
    # MAX_LEN = 4000
    # return relevant[:MAX_LEN]
    return relevant


# --- API Endpoints ---

@app.route('/scrape_and_store', methods=['POST'])
@requires_auth
def scrape_and_store():
    """
    Scrapes content from provided URLs, associates it with an agent name,
    stores it, and returns a unique code and agent name.
    """
    try:
        data = request.get_json(force=True) or {}
        urls_str = data.get('url')
        agent_name = data.get('agent_name')
        proxy_enabled = data.get('proxy_enabled', False)
        captcha_solver_enabled = data.get('captcha_solver_enabled', False)

        if not urls_str:
            return jsonify({"status": "error", "error": "URL parameter is required"}), 400
        if not agent_name:
            return jsonify({"status": "error", "error": "agent_name parameter is required"}), 400

        urls = [url.strip() for url in urls_str.split(',') if url.strip()]
        if not urls:
             return jsonify({"status": "error", "error": "No valid URLs provided"}), 400

        logger.info(f"scrape_and_store request for agent '{agent_name}' with URLs: {urls}")

        results = []
        scrape_errors = []
        for url in urls:
            logger.info(f"Scraping {url} for agent '{agent_name}'")
            result = scrape_website(url, 'beautify', proxy_enabled, captcha_solver_enabled)
            if result["status"] == "error":
                error_message = f"Error scraping {url}: {result.get('error', 'Unknown error')}"
                logger.error(error_message)
                print(error_message)
                # Store error information instead of failing the whole request
                scrape_errors.append({"url": url, "error": result.get('error', 'Unknown error')})
                # Optionally continue to next URL or return error immediately:
                # return jsonify({"status": "error", "error": error_message}), 500
                continue # Continue processing other URLs

            page_data = {"url": url, "content": []}
            scrape_data = result.get("data")

            if isinstance(scrape_data, dict) and "sections" in scrape_data:
                for section in scrape_data.get("sections", []):
                    heading_data = section.get("heading")
                    # Handle heading being a dict with 'text' or just a string
                    heading_text = heading_data.get("text", "") if isinstance(heading_data, dict) else heading_data
                    section_content = {
                        "heading": heading_text or None, # Store None if empty/missing
                        "paragraphs": section.get("paragraphs", []) # Changed from content to paragraphs
                    }
                    page_data["content"].append(section_content)
            elif isinstance(scrape_data, str) and scrape_data: # Handle simple string result
                 page_data["content"] = [{"heading": None, "paragraphs": [scrape_data]}]
            # If scrape_data is None or empty, page_data["content"] remains []

            results.append(page_data)

        unique_code = str(uuid.uuid4())
        filepath = os.path.join(SCRAPED_DATA_DIR, f"{unique_code}.txt")

        # Prepare data structure for saving
        data_to_store = {
            "agent_name": agent_name,
            "urls": urls, # Store the list of URLs attempted
            "results": results,
            "errors": scrape_errors # Include errors encountered during scraping
        }

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data_to_store, f, ensure_ascii=False, indent=4) # Store as JSON
            logger.info(f"Successfully stored scraped content for agent '{agent_name}' with code {unique_code} at {filepath}")
        except Exception as e:
            error_message = f"Error writing agent data to file {filepath}: {e}"
            logger.error(error_message)
            print(error_message)
            return jsonify({"status": "error", "error": "Failed to store scraped data"}), 500

        return jsonify({
            "status": "success",
            "unique_code": unique_code,
            "agent_name": agent_name,
            "scrape_errors": scrape_errors # Inform user about any URLs that failed
        }), 201 # 201 Created is suitable here

    except Exception as e:
        error_message = f"Internal server error in /scrape_and_store: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_message)
        print(error_message)
        return jsonify({"status": "error", "error": "An internal server error occurred"}), 500


@app.route('/ask_stored', methods=['POST'])
@requires_auth
def ask_stored():
    """
    Answers a user query based on previously stored scraped content identified by unique_code.
    """
    try:
        data = request.get_json(force=True) or {}
        unique_code = data.get('unique_code')
        user_query = data.get('user_query')

        if not unique_code:
            return jsonify({"status": "error", "error": "unique_code parameter is required"}), 400
        if not user_query:
            return jsonify({"status": "error", "error": "user_query parameter is required"}), 400

        logger.info(f"ask_stored request for code '{unique_code}' with query: '{user_query}'")

        stored_data = get_stored_content(unique_code) # Gets the whole object {"agent_name": ..., "results": ...}
        if not stored_data:
            logger.warning(f"Content not found for unique_code: {unique_code}")
            return jsonify({"status": "error", "error": f"Content not found for unique_code: {unique_code}"}), 404

        # Extract the actual scraped results list
        scraped_results = stored_data.get('results', [])
        if not scraped_results:
             logger.warning(f"No scrape results found in stored data for unique_code: {unique_code}")
             return jsonify({"status": "success", "ai_response": "I cannot provide a helpful response (no content available).", "ai_used": False})

        # Find relevant content objects based on meaningful words
        relevant_content_objects, meaningful_match_found = find_relevant_content(scraped_results, user_query)

        # If no relevant sections found OR only stop words matched, don't use AI
        if not relevant_content_objects or not meaningful_match_found:
            logger.info(f"No relevant content found or only stop words matched for query '{user_query}' in {unique_code}.")
            return jsonify({"status": "success", "ai_response": "I cannot provide a helpful response based on the stored content and your query.", "ai_used": False})

        logger.info(f"Found {len(relevant_content_objects)} relevant content objects for query.")

        # Prepare prompt for AI
        prompt_text = f"""As a knowledgeable agent, please provide a direct and conversational answer to the user's question based *only* on the provided website content below.
Do not mention that you are using the provided information.
If the answer is not found in the text, state that you cannot provide a helpful response based on the available information.
User question: "{user_query}"

Website content:
"""
        content_added = False
        for i, content_obj in enumerate(relevant_content_objects):
            prompt_text += f"\n--- Content from {content_obj.get('url', 'Unknown URL')} ---\n"
            if not isinstance(content_obj, dict) or 'content' not in content_obj:
                logger.warning(f"Skipping invalid content object format during prompt creation: {content_obj}")
                continue

            for section in content_obj.get('content', []):
                heading = section.get('heading', '') or ""
                paragraphs = section.get('paragraphs', []) or []
                if heading:
                    prompt_text += f"Heading: {heading}\n"
                    content_added = True
                if paragraphs:
                    prompt_text += "\n".join(paragraphs) + "\n"
                    content_added = True
            prompt_text += "--- End of Content ---\n"

        if not content_added:
             logger.warning(f"Relevant content objects found, but no actual text could be extracted for the prompt (code: {unique_code}).")
             return jsonify({"status": "success", "ai_response": "I cannot provide a helpful response due to an issue processing the stored content.", "ai_used": False})

        # logger.debug(f"Generated LLM Prompt:\n{prompt_text}") # Uncomment for debugging prompts

        ai_response = ask_llama(prompt_text)

        # Refined check for unhelpful responses
        unhelpful_phrases = ["sorry, i am unable", "cannot provide a helpful response", "no information available", "based on the text provided", "information is not available"]
        is_unhelpful = not ai_response or len(ai_response.strip()) < 15 or any(phrase in ai_response.lower() for phrase in unhelpful_phrases)

        if is_unhelpful:
            logger.info(f"LLM response deemed unhelpful for code {unique_code}, query '{user_query}'. Response: '{ai_response}'")
            return jsonify({"status": "success", "ai_response": "I cannot provide a helpful response based on the available information.", "ai_used": True})
        else:
            logger.info(f"LLM provided a response for code {unique_code}, query '{user_query}'.")
            return jsonify({"status": "success", "ai_response": ai_response, "ai_used": True})

    except Exception as e:
        error_message = f"Internal server error in /ask_stored: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_message)
        print(error_message)
        return jsonify({"status": "error", "error": "An internal server error occurred"}), 500


@app.route('/scrape', methods=['GET', 'POST'])
@requires_auth
def scrape():
    """
    General purpose endpoint for scraping or crawling URLs.
    Supports types: 'raw', 'beautify', 'ai', 'crawl_raw', 'crawl_beautify', 'crawl_ai'.
    'ai' types require a 'user_query'.
    """
    try:
        if request.method == 'GET':
            urls_str = request.args.get('url')
            content_type = request.args.get('type', 'beautify').lower()
            user_query = request.args.get('user_query', '')
            proxy_enabled = request.args.get('proxy_enabled', 'false').lower() == 'true'
            captcha_solver_enabled = request.args.get('captcha_solver_enabled', 'false').lower() == 'true'
        else: # POST
            try:
                data = request.get_json(force=True) or {}
            except Exception as e:
                error_message = f"Error parsing JSON in POST /scrape: {e}"
                logger.error(error_message)
                print(error_message)
                return jsonify({"status": "error", "error": "Invalid JSON payload"}), 400
            urls_str = data.get('url', '')
            content_type = data.get('type', 'beautify').lower()
            user_query = data.get('user_query', '')
            proxy_enabled = data.get('proxy_enabled', False)
            captcha_solver_enabled = data.get('captcha_solver_enabled', False)

        if not urls_str:
            return jsonify({"status": "error", "error": "URL parameter is required"}), 400

        urls = [url.strip() for url in urls_str.split(',') if url.strip()]
        if not urls:
             return jsonify({"status": "error", "error": "No valid URLs provided"}), 400

        logger.info(f"/scrape request - Type: {content_type}, URLs: {urls}, Query: '{user_query if user_query else 'N/A'}'")

        # --- Handle Simple Scrape Types ---
        if content_type in ['raw', 'beautify']:
            all_results = []
            for url in urls:
                logger.info(f"Scraping {url} (type: {content_type})")
                result = scrape_website(url, content_type, proxy_enabled, captcha_solver_enabled)
                if result["status"] == "error":
                    logger.error(f"Error scraping {url}: {result.get('error', 'Unknown error')}")
                    all_results.append({"url": url, "status": "error", "error": result.get('error', 'Unknown error')})
                else:
                     # Structure the beautify response consistently
                    if content_type == 'beautify' and isinstance(result.get("data"), dict) and "sections" in result.get("data"):
                        formatted_data = []
                        for section in result["data"]["sections"]:
                            heading_data = section.get("heading")
                            heading_text = heading_data.get("text", "") if isinstance(heading_data, dict) else heading_data
                            formatted_data.append({
                                "heading": heading_text or None,
                                "paragraphs": section.get("paragraphs", []) # Changed from content to paragraphs
                            })
                        all_results.append({"url": url, "status": "success", "content": formatted_data})
                    elif content_type == 'beautify' and isinstance(result.get("data"), str): # Handle simple string case for beautify
                         all_results.append({"url": url, "status": "success", "content": [{"heading": None, "paragraphs": [result["data"]]}]})
                    else: # Raw or other beautify cases
                        all_results.append({"url": url, "status": "success", "data": result.get("data")})

            return jsonify({"status": "success", "type": content_type, "results": all_results})

        # --- Handle AI Scrape Type ---
        elif content_type == 'ai':
            if not user_query:
                return jsonify({"status": "error", "error": "user_query parameter is required for type 'ai'"}), 400
            if _together_client is None: # Check if client could be initialized (lazy check)
                return jsonify({"status": "error", "error": "AI functionality is not available (client not initialized)."}), 503

            combined_text = ""
            errors_encountered = []
            for url in urls:
                logger.info(f"Scraping {url} for AI processing")
                result = scrape_website(url, 'beautify', proxy_enabled, captcha_solver_enabled)
                if result["status"] == "error":
                    error_message = f"Error scraping {url} for AI: {result.get('error', 'Unknown error')}"
                    logger.error(error_message)
                    errors_encountered.append({"url": url, "error": error_message})
                    continue # Skip this URL

                scrape_data = result.get("data")
                if isinstance(scrape_data, dict) and "sections" in scrape_data:
                    for sec in scrape_data.get("sections", []):
                        heading = sec.get("heading")
                        heading_text = heading.get("text", "") if isinstance(heading, dict) else heading
                        paragraphs = sec.get("paragraphs", []) # Changed from content to paragraphs
                        if heading_text:
                            combined_text += f"\nHeading: {heading_text}\n"
                        if paragraphs:
                            combined_text += "\n".join(paragraphs) + "\n"
                elif isinstance(scrape_data, str) and scrape_data:
                    combined_text += f"\n{scrape_data}\n"

            if not combined_text.strip():
                logger.warning(f"No text content could be extracted from URLs {urls} for AI query.")
                return jsonify({
                    "status": "success",
                    "type": "ai",
                    "ai_response": "Could not extract text content from the website(s).",
                    "ai_used": False,
                    "errors": errors_encountered
                })
            
            # Find relevant sentences (replace placeholder)
            relevant_sentences = find_relevant_sentences(combined_text, user_query)

            if not relevant_sentences:
                logger.info(f"No relevant sentences found for query '{user_query}'.")
                return jsonify({
                    "status": "success",
                    "type": "ai",
                    "ai_response": "I cannot provide a helpful response based on the website content and your query.",
                    "ai_used": False,
                    "errors": errors_encountered
                })
            else:
                logger.info(f"Found {len(relevant_sentences)} relevant sentences for AI query.")
                relevant_content = "\n".join(relevant_sentences)

                # Optional: Truncate context if needed for LLM
                # MAX_CONTEXT_LEN = 8000 # Example limit
                # if len(relevant_content) > MAX_CONTEXT_LEN:
                #    relevant_content = relevant_content[:MAX_CONTEXT_LEN] + "\n... [truncated]"

                ai_prompt = f"""As a knowledgeable agent, please provide a direct and conversational answer to the user's question based *only* on the provided website content below. Do not mention that you are using the provided information. If the answer is not found in the text, state that you cannot provide a helpful response based on the available information. User question: "{user_query}" Website content: \"\"\" {relevant_content} \"\"\" Answer:"""
                # logger.debug(f"Generated LLM Prompt (scrape/ai):\n{ai_prompt}") # Uncomment for debugging

                ai_response = ask_llama(ai_prompt)

                unhelpful_phrases = ["sorry, i am unable", "cannot provide a helpful response", "no information available", "based on the text provided", "information is not available"]
                is_unhelpful = not ai_response or len(ai_response.strip()) < 15 or any(phrase in ai_response.lower() for phrase in unhelpful_phrases)

                if is_unhelpful:
                    logger.info(f"LLM response deemed unhelpful for scrape/ai query '{user_query}'. Response: '{ai_response}'")
                    return jsonify({
                        "status": "success",
                        "type": "ai",
                        "ai_response": "I cannot provide a helpful response based on the available information.",
                        "ai_used": True,
                        "errors": errors_encountered
                    })
                else:
                    logger.info(f"LLM provided response for scrape/ai query '{user_query}'.")
                    return jsonify({
                        "status": "success",
                        "type": "ai",
                        "ai_response": ai_response,
                        "ai_used": True,
                        "errors": errors_encountered
                    })

        # --- Handle Crawl Types ---
        elif content_type.startswith("crawl_"):
            all_crawl_data = []
            crawl_mode = content_type # e.g., 'crawl_raw', 'crawl_beautify', 'crawl_ai'
            for url in urls:
                # process_crawl handles internal scraping and link finding
                crawl_results = crawl_website(url, crawl_mode.replace("crawl_", ""), user_query, proxy_enabled, captcha_solver_enabled) # Pass new args
                all_crawl_data.extend(crawl_results) # Add results from this base URL

            # Format output based on crawl mode
            if crawl_mode == "crawl_beautify":
                # For beautify, return structured content directly
                formatted_crawl_data = []
                for item in all_crawl_data:
                    if "content" in item:
                        formatted_crawl_data.append({
                            "url": item.get("url"),
                            "content": item["content"]
                        })
                    elif "error" in item:
                        formatted_crawl_data.append({
                            "url": item.get("url"),
                            "error": item["error"]
                        })
                return jsonify({"status": "success", "type": crawl_mode, "results": formatted_crawl_data})
            elif crawl_mode == "crawl_raw":
                # For raw, return raw_data directly
                formatted_crawl_data = []
                for item in all_crawl_data:
                    if "raw_data" in item:
                        formatted_crawl_data.append({
                            "url": item.get("url"),
                            "raw_data": item["raw_data"]
                        })
                    elif "error" in item:
                        formatted_crawl_data.append({
                            "url": item.get("url"),
                            "error": item["error"]
                        })
                return jsonify({"status": "success", "type": crawl_mode, "results": formatted_crawl_data})
            elif crawl_mode == "crawl_ai":
                if not user_query:
                    return jsonify({"status": "error", "error": "user_query parameter is required for type 'crawl_ai'"}), 400
                if _together_client is None: # Check if client could be initialized (lazy check)
                    return jsonify({"status": "error", "error": "AI functionality is not available (client not initialized)."}), 503

                all_text_content = ""
                crawl_errors = []
                processed_urls = set() # To avoid duplicate content from redirected URLs or same pages in different crawl paths
                for item in all_crawl_data:
                    url = item.get("url")
                    if url in processed_urls:
                        continue # Avoid duplicate text from multiple paths
                    processed_urls.add(url)

                    if "content" in item:
                        all_text_content += f"\n\n--- Content from {url} ---\n"
                        for section in item["content"]:
                            heading = section.get('heading', '') or ""
                            paragraphs = section.get('paragraphs', []) or []
                            if heading:
                                all_text_content += f"\nHeading: {heading}\n"
                            if paragraphs:
                                all_text_content += "\n".join(paragraphs) + "\n"
                    elif "error" in item:
                        crawl_errors.append({"url": url, "error": item["error"]})

                if not all_text_content.strip():
                    logger.warning(f"No text content could be extracted from crawled URLs starting from {urls} for AI query.")
                    return jsonify({
                        "status": "success",
                        "type": crawl_mode,
                        "ai_response": "Could not extract text content from the crawled website(s).",
                        "ai_used": False,
                        "errors": crawl_errors
                    })

                # Find relevant sentences (replace placeholder)
                relevant_sentences = find_relevant_sentences(all_text_content, user_query)

                if not relevant_sentences:
                    logger.info(f"No relevant sentences found for query '{user_query}' in crawled content.")
                    return jsonify({
                        "status": "success",
                        "type": crawl_mode,
                        "ai_response": "I cannot provide a helpful response based on the crawled website content and your query.",
                        "ai_used": False,
                        "errors": crawl_errors
                    })
                
                else:
                    logger.info(f"Found {len(relevant_sentences)} relevant sentences from crawl for AI query.")
                    relevant_content = "\n".join(relevant_sentences)

                    # Optional: Truncate context
                    # MAX_CONTEXT_LEN = 8000
                    # if len(relevant_content) > MAX_CONTEXT_LEN:
                    #    relevant_content = relevant_content[:MAX_CONTEXT_LEN] + "\n... [truncated]"

                    ai_prompt = f"""As a knowledgeable agent, please provide a direct and conversational answer to the user's question based *only* on the provided website content gathered from crawling multiple pages.
Do not mention that you are using the provided information or that the content comes from multiple pages.
If the answer is not found in the text, state that you cannot provide a helpful response based on the available information.
User question: "{user_query}"

Website content:
\"\"\"
{relevant_content}
\"\"\"

Answer:"""
                    # logger.debug(f"Generated LLM Prompt (crawl/ai):\n{ai_prompt}") # Uncomment for debugging

                    ai_response = ask_llama(ai_prompt)

                    unhelpful_phrases = ["sorry, i am unable", "cannot provide a helpful response", "no information available", "based on the text provided", "information is not available"]
                    is_unhelpful = not ai_response or len(ai_response.strip()) < 15 or any(phrase in ai_response.lower() for phrase in unhelpful_phrases)

                    if is_unhelpful:
                        logger.info(f"LLM response deemed unhelpful for crawl/ai query '{user_query}'. Response: '{ai_response}'")
                        return jsonify({
                            "status": "success",
                            "type": crawl_mode,
                            "ai_response": "I cannot provide a helpful response based on the available information from the crawled website.",
                            "ai_used": True,
                            "errors": crawl_errors
                        })
                    else:
                        logger.info(f"LLM provided response for crawl/ai query '{user_query}'.")
                        return jsonify({
                            "status": "success",
                            "type": crawl_mode, # Keep original type
                            "ai_response": ai_response,
                            "ai_used": True,
                            "errors": crawl_errors
                        })
            else: # Fallback for unknown crawl_ type
                return jsonify({"status": "error", "error": f"Invalid crawl type specified: {crawl_mode}"}), 400

        else: # --- Handle Invalid Type Parameter ---
            return jsonify({"status": "error", "error": f"Invalid type parameter '{content_type}'. Valid types: raw, beautify, ai, crawl_raw, crawl_beautify, crawl_ai."}), 400

    except Exception as e:
        error_message = f"Internal server error in /scrape: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_message)
        print(error_message)
        return jsonify({"status": "error", "error": "An internal server error occurred"}), 500


# --- NEW: Agent Management Endpoints ---

@app.route('/agents', methods=['GET'])
@requires_auth
def get_all_agents():
    """Retrieves a list of all stored agents (name, id, urls)."""
    agents = []
    try:
        logger.info("Request received for /agents")
        for filename in os.listdir(SCRAPED_DATA_DIR):
            if filename.endswith(".txt"):
                unique_code = filename[:-4] # Remove .txt extension
                filepath = os.path.join(SCRAPED_DATA_DIR, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if isinstance(data, dict) and 'agent_name' in data and 'urls' in data:
                            agents.append({
                                "agent_name": data.get("agent_name", "Unknown"),
                                "agent_id": unique_code,
                                "urls": data.get("urls", [])
                            })
                        else:
                            logger.warning(f"File {filename} does not contain expected agent structure. Skipping.")
                except json.JSONDecodeError:
                    logger.error(f"Could not decode JSON from file: {filename}. Skipping.")
                except Exception as e:
                    logger.error(f"Error reading or processing file {filename}: {e}. Skipping.")
        logger.info(f"Returning {len(agents)} agents.")
        return jsonify({"status": "success", "agents": agents})
    except Exception as e:
        error_message = f"Internal server error in /agents: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_message)
        print(error_message)
        return jsonify({"status": "error", "error": "An internal server error occurred"}), 500


@app.route('/update_agent/<unique_code>', methods=['PUT'])
@requires_auth
def update_agent(unique_code):
    """
    Updates an existing agent's scraped content by adding new URLs.
    New URLs will be scraped and their content appended to the existing agent's data.
    """
    logger.info(f"Update agent request for code: {unique_code}")
    filepath = os.path.join(SCRAPED_DATA_DIR, f"{unique_code}.txt")

    if not os.path.exists(filepath):
        logger.warning(f"Update failed: Agent with code {unique_code} not found at {filepath}.")
        return jsonify({"status": "error", "error": f"Agent with unique_code {unique_code} not found"}), 404

    try:
        # 1. Load existing data
        existing_data = get_stored_content(unique_code)
        if not existing_data:
            return jsonify({"status": "error", "error": "Could not load existing agent data"}), 500
        
        # Preserve original agent_name if available, or default
        original_agent_name = existing_data.get("agent_name", "Unknown Agent")
        existing_urls = set(existing_data.get("urls", []))
        existing_results = existing_data.get("results", [])
        existing_errors = existing_data.get("errors", [])

    except Exception as e:
        logger.error(f"Error loading existing agent data for {unique_code} during update: {e}. Proceeding with default name.")
        # Decide if you want to proceed or fail here
        # return jsonify({"status": "error", "error": "Could not read existing agent data"}), 500

    # 2. Get new URLs from request body
    try:
        data = request.get_json(force=True) or {}
        urls_str = data.get('url')
        proxy_enabled = data.get('proxy_enabled', False)
        captcha_solver_enabled = data.get('captcha_solver_enabled', False)

        if not urls_str:
            return jsonify({"status": "error", "error": "New 'url' parameter (comma-separated string) is required in the request body"}), 400
        new_urls = [url.strip() for url in urls_str.split(',') if url.strip()]
        if not new_urls:
            return jsonify({"status": "error", "error": "No valid new URLs provided"}), 400
        logger.info(f"Update request for agent '{original_agent_name}' ({unique_code}) with new URLs: {new_urls}")
    except Exception as e:
        logger.error(f"Error parsing JSON for agent update {unique_code}: {e}")
        return jsonify({"status": "error", "error": "Invalid JSON payload for update"}), 400

    # 3. Scrape new URLs (only those not already associated with the agent)
    urls_to_scrape = [url for url in new_urls if url not in existing_urls]
    
    current_scrape_results = []
    current_scrape_errors = []

    for url in urls_to_scrape:
        logger.info(f"Updating agent {unique_code}: Scraping {url}")
        result = scrape_website(url, 'beautify', proxy_enabled, captcha_solver_enabled) # Pass new args
        if result["status"] == "error":
            error_message = f"Error scraping {url} during update: {result.get('error', 'Unknown error')}"
            logger.error(error_message)
            current_scrape_errors.append({"url": url, "error": result.get('error', 'Unknown error')})
            continue # Continue with other URLs

        page_data = {"url": url, "content": []}
        scrape_data = result.get("data")
        if isinstance(scrape_data, dict) and "sections" in scrape_data:
            for section in scrape_data.get("sections", []):
                heading_data = section.get("heading")
                heading_text = heading_data.get("text", "") if isinstance(heading_data, dict) else heading_data
                section_content = {
                    "heading": heading_text or None,
                    "paragraphs": section.get("paragraphs", []) # Changed from content to paragraphs
                }
                page_data["content"].append(section_content)
        elif isinstance(scrape_data, str) and scrape_data:
            page_data["content"] = [{"heading": None, "paragraphs": [scrape_data]}]
        current_scrape_results.append(page_data)

    # 4. Combine and save updated data
    updated_urls = list(existing_urls.union(set(new_urls))) # Combine all URLs
    updated_results = existing_results + current_scrape_results
    updated_errors = existing_errors + current_scrape_errors

    updated_data_to_store = {
        "agent_name": original_agent_name,
        "urls": updated_urls,
        "results": updated_results,
        "errors": updated_errors
    }

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(updated_data_to_store, f, ensure_ascii=False, indent=4)
        logger.info(f"Successfully updated agent '{original_agent_name}' with code {unique_code}.")
        return jsonify({
            "status": "success",
            "unique_code": unique_code,
            "agent_name": original_agent_name,
            "newly_scraped_urls": [res["url"] for res in current_scrape_results],
            "new_scrape_errors": current_scrape_errors,
            "message": f"Agent {original_agent_name} updated successfully with new URLs."
        }), 200

    except Exception as e:
        error_message = f"Error writing updated agent data to file {filepath}: {e}"
        logger.error(error_message)
        print(error_message)
        return jsonify({"status": "error", "error": "Failed to save updated agent data"}), 500


@app.route('/delete_agent/<unique_code>', methods=['DELETE'])
@requires_auth
def delete_agent(unique_code):
    """Deletes a specific agent."""
    filepath = os.path.join(SCRAPED_DATA_DIR, f"{unique_code}.txt")
    logger.info(f"Delete request received for agent code: {unique_code}")
    if not os.path.exists(filepath):
        logger.warning(f"Deletion failed: Agent with code {unique_code} not found at {filepath}.")
        return jsonify({"status": "error", "error": f"Agent with unique_code {unique_code} not found"}), 404
    try:
        os.remove(filepath)
        logger.info(f"Successfully deleted agent data file: {filepath}")
        return jsonify({"status": "success", "message": f"Agent {unique_code} deleted successfully."}), 200 # OK or 204 No Content
    except OSError as e:
        error_message = f"Error deleting agent data file {filepath}: {e}"
        logger.error(error_message)
        print(error_message)
        return jsonify({"status": "error", "error": "Failed to delete agent data file"}), 500
    except Exception as e:
        error_message = f"Unexpected error during agent deletion {unique_code}: {e}\n{traceback.format_exc()}"
        logger.error(error_message)
        print(error_message)
        return jsonify({"status": "error", "error": "An unexpected error occurred during deletion"}), 500


@app.route('/get_stored_file/<unique_code>', methods=['GET'])
@requires_auth
def get_stored_file(unique_code):
    """Retrieves the full content of a stored agent file."""
    logger.info(f"Request to get stored file for code: {unique_code}")
    content = get_stored_content(unique_code) # This now gets the full object
    if content:
        return jsonify({"status": "success", "unique_code": unique_code, "content": content}) # Return the full object
    else:
        logger.warning(f"Stored file not found for code: {unique_code}")
        return jsonify({"status": "error", "error": f"Content not found for unique_code: {unique_code}"}), 404

# --- Main Execution ---
if __name__ == '__main__':
    print(f"Starting Flask server on host 0.0.0.0 port 5000")
    print(f"Serving scraped data from: {os.path.abspath(SCRAPED_DATA_DIR)}")
    # app.run(debug=True, host='0.0.0.0', port=5000) # For local development
    # Using waitress for production deployment
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=os.environ.get("PORT", 5000))
    except ImportError:
        print("Waitress not found, falling back to Flask's default development server.")
        print("For production, consider installing waitress: pip install waitress")
        app.run(debug=True, host='0.0.0.0', port=os.environ.get("PORT", 5000))
