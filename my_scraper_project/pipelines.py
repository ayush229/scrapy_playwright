import json
import logging
from itemadapter import ItemAdapter
# from scrapy.utils.project import get_project_settings # This might not be needed anymore if queue is passed directly

logger = logging.getLogger(__name__)

class JsonWriterPipeline:
    def open_spider(self, spider):
        # Now, the queue is expected to be set directly on the spider instance
        # if it's passed during spider initialization.
        # We no longer rely on get_project_settings for the queue.
        self.results_queue = getattr(spider, 'results_queue', None) 
        if not self.results_queue:
            spider.logger.error("SCRAPY_RESULTS_QUEUE (results_queue) not found on spider instance. Results will not be passed back.")
        else:
            spider.logger.info("Scrapy results will be put into the shared queue.")

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        if self.results_queue:
            item_dict = adapter.asdict()
            self.results_queue.put(item_dict)
            spider.logger.info(f"Item processed and added to queue: {item_dict.get('url', 'N/A')}")
        else:
            spider.logger.warning(f"Item processed but no queue found to return results: {adapter.asdict().get('url', 'N/A')}")
        return item
