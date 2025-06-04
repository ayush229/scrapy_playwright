import json
import logging
from itemadapter import ItemAdapter
from scrapy.utils.project import get_project_settings

logger = logging.getLogger(__name__)

class JsonWriterPipeline:
    def open_spider(self, spider):
        settings = get_project_settings()
        self.results_queue = settings.get('SCRAPY_RESULTS_QUEUE')
        if not self.results_queue:
            spider.logger.error("SCRAPY_RESULTS_QUEUE not found in settings. Results will not be passed back to Flask.")
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
