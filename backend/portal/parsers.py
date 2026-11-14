from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser

class ObjectJSONParser(JSONParser):
    """Every JSON request body must be an object, so handlers can rely on a mapping."""
    def parse(self,stream,media_type=None,parser_context=None):
        data=super().parse(stream,media_type,parser_context)
        if not isinstance(data,dict):
            raise ParseError('Expected a JSON object.')
        return data
