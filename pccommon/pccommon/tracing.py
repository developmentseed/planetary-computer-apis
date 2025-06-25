import json
import logging
import re
from typing import Any, List, Optional, Tuple, Union, cast

from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
from fastapi import Request
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, Tracer
from starlette.datastructures import QueryParams

from pccommon.config import get_apis_config
from pccommon.constants import (
    HTTP_METHOD,
    HTTP_PATH,
    HTTP_URL,
    X_AZURE_REF,
    X_REQUEST_ENTITY,
)
from pccommon.logging import request_to_path
from pccommon.utils import get_request_ip

_config = get_apis_config()
logger = logging.getLogger(__name__)


tracer: Optional[Tracer]

if _config.app_insights_instrumentation_key:
    tracer_provider = TracerProvider()
    trace.set_tracer_provider(tracer_provider)

    azure_exporter = AzureMonitorTraceExporter(
        connection_string="InstrumentationKey="
        + _config.app_insights_instrumentation_key
    )

    span_processor = BatchSpanProcessor(azure_exporter)
    tracer_provider.add_span_processor(span_processor)

    tracer = trace.get_tracer(__name__)
else:
    tracer = None


async def trace_request(
    service_name: str,
    request: Request,
) -> None:
    """Construct a request trace with custom dimensions"""
    request_path = request_to_path(request).strip("/")

    if _should_trace_request(request):
        assert tracer
        with tracer.start_as_current_span("main", kind=SpanKind.SERVER) as span:
            (collection_id, item_id) = await _collection_item_from_request(
                service_name, request
            )

            request.state.parent_span = span

            span.set_attribute("ref_id", request.headers.get(X_AZURE_REF) or "")
            span.set_attribute(
                "request_entity", request.headers.get(X_REQUEST_ENTITY) or ""
            )
            span.set_attribute("request_ip", get_request_ip(request) or "")
            span.set_attribute(HTTP_METHOD, str(request.method))
            span.set_attribute(HTTP_URL, str(request.url))
            span.set_attribute(HTTP_PATH, request_path)
            span.set_attribute("service", service_name)
            span.set_attribute("in-server", "true")

            if collection_id is not None:
                span.set_attribute("collection", collection_id)
                if item_id is not None:
                    span.set_attribute("item", item_id)


collection_id_re = re.compile(
    r".*/collections/?(?P<collection_id>[a-zA-Z0-9\-\%]+)?(/items/(?P<item_id>.*))?.*"  # noqa
)


async def _collection_item_from_request(
    service_name: str,
    request: Request,
) -> Tuple[Optional[str], Optional[str]]:
    """Attempt to get collection and item ids from the request path or querystring."""
    url = request.url
    try:
        collection_id_match = collection_id_re.match(f"{url}")
        if collection_id_match:
            collection_id = cast(
                Optional[str], collection_id_match.group("collection_id")
            )
            item_id = cast(Optional[str], collection_id_match.group("item_id"))
            return (collection_id, item_id)
        else:
            collection_id = request.query_params.get("collection")
            # Some endpoints, like preview/, take an `items` parameter, but
            # conventionally it is a single item id
            item_id = request.query_params.get("item") or request.query_params.get(
                "items"
            )
            return (collection_id, item_id)
    except Exception as e:
        logger.exception(e)
        return (None, None)


def _should_trace_request(request: Request) -> bool:
    """
    Determine if we should trace a request.
        - Not a HEAD request
        - Not a health check endpoint
    """
    return (
        (tracer is not None)
        and request.method.lower() != "head"
        and not request.url.path.strip("/").endswith("_mgmt/ping")
    )


def _parse_cqljson(cql: dict) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse the collection id from a CQL-JSON filter.

    The CQL-JSON filter is a bit of a special case. It's a JSON object in either
    CQL or CQL2 syntax. Parse the object and look for the collection and item
    ids. If multiple collections or items are found, format them to a CSV.
    """
    collections = _iter_cql(cql, property_name="collection")
    ids = _iter_cql(cql, property_name="id")

    if isinstance(collections, list):
        collections = ",".join(collections)
    if isinstance(ids, list):
        ids = ",".join(ids)

    return (collections, ids)


def _parse_queryjson(query: dict) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse the collection and item ids from a traditional STAC API Item Search body.

    The query is a JSON object with relevant keys, "collections" and "ids".
    """
    collection_ids = query.get("collections")
    item_ids = query.get("ids")

    # Collection and ids are List[str] per the spec,
    # but the client may allow just a single item
    if isinstance(collection_ids, list):
        collection_ids = ",".join(collection_ids)
    if isinstance(item_ids, list):
        item_ids = ",".join(item_ids)

    return (collection_ids, item_ids)


def _iter_cql(cql: dict, property_name: str) -> Optional[Union[str, List[str]]]:
    """
    Recurse through a CQL or CQL2 filter body, returning the value of the
    provided property name, if found. Typical usage will be to provide
    `collection` and `id`.
    """
    if cql is None:
        return None
    for _, v in cql.items():
        if isinstance(v, dict):
            result = _iter_cql(v, property_name)
            if result is not None:
                return result
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    if "property" in item:
                        if item["property"] == property_name:
                            return v[1]
                    else:
                        result = _iter_cql(item, property_name)
                        if result is not None:
                            return result
    # No collection was found
    return None


def add_stac_attributes_from_search(search_json: str, request: Request) -> None:
    """
    Try to add the Collection ID and Item ID from a search to the current span.
    """
    collection_id, item_id = parse_collection_from_search(
        json.loads(search_json), request.method, request.query_params
    )

    current_span: Union[Optional[Any], Span]
    current_span = trace.get_current_span()
    if not current_span.is_recording():
        current_span = getattr(request.state, "parent_span", None)

    if current_span and current_span.is_recording():
        if collection_id is not None:
            current_span.set_attribute("collection", collection_id)
            if item_id is not None:
                current_span.set_attribute("item", item_id)
    else:
        logger.warning("No active or parent span available for adding attributes.")


def parse_collection_from_search(
    body: dict,
    method: str,
    query_params: QueryParams,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse the collection id from a search request.

    The search endpoint is a bit of a special case. If it's a GET, the collection
    and item ids are in the querystring. If it's a POST, the collection and item may
    be in either a CQL-JSON or CQL2-JSON filter body, or a query/stac-ql body.
    """
    if method.lower() == "get":
        collection_id = query_params.get("collections")
        item_id = query_params.get("ids")
        return (collection_id, item_id)
    elif method.lower() == "post":
        try:
            if body.get("collections") is not None:
                return _parse_queryjson(body)
            elif "filter" in body:
                return _parse_cqljson(body["filter"])
        except json.JSONDecodeError as e:
            logger.warning(
                "Unable to parse search body as JSON. Ignoring collection"
                f"parameter. {e}"
            )
    return (None, None)
