# Parsec Cloud (https://parsec.cloud) Copyright (c) BSLv1.1 (eventually AGPLv3) 2016-2021 Scille SAS

import re
import attr
from typing import List, Dict, Optional, Pattern, Tuple
import mimetypes
from urllib.parse import parse_qs, urlsplit, urlunsplit, urlencode
import importlib_resources
import h11

from parsec.backend.config import BackendConfig
from parsec.backend import static as http_static_module
from parsec.backend.templates import get_template

try:
    from typing import Protocol
except ImportError:
    from typing_extensions import Protocol  # type: ignore


class RequestHandler(Protocol):
    async def __call__(self, req: "HTTPRequest", **kwargs: str) -> "HTTPResponse":
        ...


@attr.s(slots=True, auto_attribs=True)
class HTTPRequest:
    method: str
    path: str
    query: Dict[str, List[str]]
    headers: Dict[bytes, bytes]

    async def get_data(self) -> bytes:
        # TODO: we don't need this yet ;-)
        raise NotImplementedError()

    @classmethod
    def from_h11_req(cls, h11_req: h11.Request) -> "HTTPRequest":
        # h11 makes sure the headers and target are ISO-8859-1
        target_split = urlsplit(h11_req.target.decode("ISO-8859-1"))
        query_params = parse_qs(target_split.query)

        # Note h11 already does normalization on headers
        # (see https://h11.readthedocs.io/en/latest/api.html?highlight=request#headers-format)
        return cls(
            method=h11_req.method.decode(),
            path=target_split.path,
            query=query_params,
            headers=dict(h11_req.headers),
        )


@attr.s(slots=True, auto_attribs=True)
class HTTPResponse:
    status_code: int
    headers: Dict[bytes, bytes]
    data: Optional[bytes]

    @classmethod
    def build_html(
        cls, status_code: int, data: str, headers: Optional[Dict[bytes, bytes]] = None
    ) -> "HTTPResponse":
        headers = headers or {}
        headers[b"content-type"] = b"text/html;charset=utf-8"
        return cls.build(status_code=status_code, headers=headers, data=data.encode("utf-8"))

    @classmethod
    def build(
        cls,
        status_code: int,
        headers: Optional[Dict[bytes, bytes]] = None,
        data: Optional[bytes] = None,
    ) -> "HTTPResponse":
        headers = headers or {}
        return cls(status_code=status_code, headers=headers, data=data)


class HTTPComponent:
    def __init__(self, config: BackendConfig):
        self._config = config
        self.route_mapping: List[Tuple[Pattern[str], RequestHandler]] = [
            (re.compile(r"^/?$"), self._http_root),
            (re.compile(r"^/redirect/(?P<path>.*)$"), self._http_redirect),
            (re.compile(r"^/static/(?P<path>.*)$"), self._http_static),
        ]

    async def _http_404(self, req: HTTPRequest, **kwargs: str) -> HTTPResponse:
        data = get_template("404.html").render()
        return HTTPResponse.build_html(404, data=data)

    async def _http_root(self, req: HTTPRequest, **kwargs: str) -> HTTPResponse:
        data = get_template("index.html").render()
        return HTTPResponse.build_html(200, data=data)

    async def _http_redirect(self, req: HTTPRequest, **kwargs: str) -> HTTPResponse:
        path = kwargs["path"]
        if not self._config.backend_addr:
            return HTTPResponse.build(501, data=b"Url redirection is not available")
        backend_addr_split = urlsplit(self._config.backend_addr.to_url())

        # Build location url by merging provided path and query params with backend addr
        location_url_query_params = req.query.copy()
        # `no_ssl` param depends of backend_addr, hence it cannot be overwritten !
        location_url_query_params.pop("no_ssl", None)
        location_url_query_params.update(parse_qs(backend_addr_split.query))
        location_url_query = urlencode(query=location_url_query_params, doseq=True)
        location_url = urlunsplit(
            (backend_addr_split.scheme, backend_addr_split.netloc, path, location_url_query, None)
        )

        return HTTPResponse.build(302, headers={b"location": location_url.encode("ascii")})

    async def _http_static(self, req: HTTPRequest, **kwargs: str) -> HTTPResponse:
        path = kwargs["path"]
        if path == "__init__.py":
            return HTTPResponse.build(404)

        try:
            # Note we don't support nested resources, this is fine for the moment
            # and it prevent us from malicious path containing `..`
            data = importlib_resources.read_binary(http_static_module, path)
        except (FileNotFoundError, ValueError):
            return HTTPResponse.build(404)

        headers = {}
        content_type, _ = mimetypes.guess_type(path)
        if content_type:
            headers[b"content-Type"] = content_type.encode("ascii")
        return HTTPResponse.build(200, headers=headers, data=data)

    async def handle_request(self, req: HTTPRequest) -> HTTPResponse:
        # Only GET requests are supported
        if req.method != "GET":
            return HTTPResponse.build(405)

        # Loop over patterns
        for pattern, handler in self.route_mapping:
            # Match against the path
            match = pattern.match(req.path)
            # Run the request handler
            if match:
                route_args = match.groupdict()
                break
        else:
            handler = self._http_404
            route_args = {}

        return await handler(req, **route_args)
