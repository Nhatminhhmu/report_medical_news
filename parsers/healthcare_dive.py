from ._common import collect_listing

def collect(source):
    return collect_listing(source, allowed=('/news/', '/'), blocked=('/events', '/about', '/podcasts', '/webinars'), max_items=35)
