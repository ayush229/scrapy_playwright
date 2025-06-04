import scrapy

class ScrapedItem(scrapy.Item):
    url = scrapy.Field()
    content = scrapy.Field()
    raw_data = scrapy.Field()
    error = scrapy.Field()
