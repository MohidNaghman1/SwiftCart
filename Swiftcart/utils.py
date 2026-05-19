from rest_framework.response import Response


# Build a standardized API response payload.
def api_response(status, msg, data=None, http_status=200):
    response_body = {
        'status': status,
        'msg': msg,
        'data': data or {},
    }
    return Response(response_body, status=http_status)


# Return a fully qualified URL for a stored media path.
def build_absolute_uri(request, path):
    if not path:
        return None
    return request.build_absolute_uri(path)