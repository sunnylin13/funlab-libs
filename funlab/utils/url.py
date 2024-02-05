from urllib import parse

def get_request_url(request):
    if request.method == 'POST':
        post_param = parse.unquote(request.body.decode('utf-8'))
        url = parse.unquote(request.url + '?' + post_param)
    else:
        url = parse.unquote(request.url)
    return url

def get_request_post_param(request):
    if request.method == 'POST':
        post_param = dict(parse.parse_qsl(parse.unquote(request.body.decode('utf-8'))))
    else:
        post_param = dict(parse.parse_qsl(parse.urlsplit(request.url).query))
    return post_param

def get_request_post_param_by_url(url):
    return dict(parse.parse_qsl(parse.urlsplit(url).query))