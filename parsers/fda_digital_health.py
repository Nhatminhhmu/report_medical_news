from ._common import collect_listing

def collect(source):
    return collect_listing(source, allowed=['/medical-devices/digital-health', '/medical-devices/', '/news-events/'], blocked=('/contact-fda', '/about-fda'), max_items=35)
