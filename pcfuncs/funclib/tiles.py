import asyncio
import io
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union
from urllib.parse import urlsplit

import aiohttp
import mercantile
from funclib.models import RenderOptions
from funclib.raster import (
    Bbox,
    ExportFormats,
    GDALRaster,
    PILRaster,
    Raster,
    RasterExtent,
)
from funclib.settings import BaseExporterSettings
from mercantile import Tile
from PIL import Image

from pccommon.backoff import BackoffStrategy, with_backoff_async

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Raster)
U = TypeVar("U", bound="TileSet")


class TilerError(Exception):
    def __init__(self, msg: str, resp: aiohttp.ClientResponse):
        super().__init__(msg)
        self.resp = resp


@dataclass
class TileSetDimensions:
    tile_cols: int
    tile_rows: int
    total_cols: int
    total_rows: int
    tile_size: int


def get_tileset_dimensions(tiles: List[Tile], tile_size: int) -> TileSetDimensions:
    tile_cols = len(set([tile.x for tile in tiles]))
    tile_rows = int(len(tiles) / tile_cols)

    return TileSetDimensions(
        tile_cols=tile_cols,
        tile_rows=tile_rows,
        total_cols=tile_cols * tile_size,
        total_rows=tile_rows * tile_size,
        tile_size=tile_size,
    )


class TileSet(ABC, Generic[T]):
    def __init__(
        self,
        tile_url: str,
        render_options: RenderOptions,
        max_concurrency: int = 10,
        tile_size: int = 512,
    ) -> None:
        self.tile_url = tile_url
        self.render_options = render_options
        self.tile_size = tile_size

        self._async_limit = asyncio.Semaphore(max_concurrency)

    def get_tile_url(self, z: int, x: int, y: int) -> str:
        url = (
            self.tile_url.replace("{x}", str(x))
            .replace("{y}", str(y))
            .replace("{z}", str(z))
        )
        url += f"?{self.render_options.encoded_query_string}"
        return url

    @abstractmethod
    async def get_mosaic(self, tiles: List[Tile]) -> T: ...

    @staticmethod
    def get_covering_tiles(
        bbox: Bbox,
        target_cols: int,
        target_rows: int,
        tile_size: int = 512,
        min_zoom: Optional[int] = None,
    ) -> List[Tile]:
        """Gets tiles covering the given geometry at a zoom level
        that can produce a target_cols x target_rows image."""

        if min_zoom:
            candidate_zoom = min_zoom
        else:
            candidate_zoom = 3

        while True:
            sw_tile = mercantile.tile(bbox.xmin, bbox.ymin, candidate_zoom)
            ne_tile = mercantile.tile(bbox.xmax, bbox.ymax, candidate_zoom)
            x_diff = ne_tile.x - sw_tile.x
            y_diff = sw_tile.y - ne_tile.y
            width = (x_diff - 1) * tile_size
            height = (y_diff - 1) * tile_size
            if width < target_cols or height < target_rows:
                candidate_zoom += 1
            else:
                break

        return [
            tile
            for tile in mercantile.tiles(
                bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax, candidate_zoom
            )
        ]

    @classmethod
    async def create(
        cls: Type[U],
        cql: Dict[str, Any],
        render_options: RenderOptions,
        settings: BaseExporterSettings,
        data_api_url_override: Optional[str] = None,
    ) -> U:
        register_url = settings.get_register_url(data_api_url_override)

        async with aiohttp.ClientSession() as session:
            # Register the search and get the tilejson_url back
            resp = await session.post(register_url, json=cql)
            mosaic_info = await resp.json()
            tilejson_href = [
                link["href"]
                for link in mosaic_info["links"]
                if link["rel"] == "tilejson"
            ][0]
            tile_url = f"{tilejson_href}?{render_options.encoded_query_string}"

            # Get the full tile path template
            resp = await session.get(tile_url)
            tilejson = await resp.json()
            tile_url = tilejson["tiles"][0]

            scheme, netloc, path, _, _ = urlsplit(tile_url)
            tile_url = f"{scheme}://{netloc}{path}".replace("@1x", "@2x")

            return cls(tile_url, render_options)


class GDALTileSet(TileSet[GDALRaster]):
    async def get_mosaic(self, tiles: List[Tile]) -> GDALRaster:
        raise NotImplementedError()


class PILTileSet(TileSet[PILRaster]):
    async def _get_tile(self, url: str) -> io.BytesIO:
        async def _f() -> io.BytesIO:
            async with aiohttp.ClientSession() as session:
                async with self._async_limit:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            return io.BytesIO(await resp.read())
                        else:
                            raise TilerError(
                                f"Error downloading tile: {url}", resp=resp
                            )

        try:
            return await with_backoff_async(
                _f,
                is_throttle=lambda e: isinstance(e, TilerError),
                strategy=BackoffStrategy(waits=[0.2, 0.5, 0.75, 1, 2]),
            )
        except Exception:
            logger.warning(f"Tile request failed with backoff: {url}")
            img_bytes = Image.new("RGB", (self.tile_size, self.tile_size), "gray")
            empty = io.BytesIO()
            img_bytes.save(empty, format="png")
            return empty

    async def get_mosaic(self, tiles: List[Tile]) -> PILRaster:
        tasks: List[asyncio.Future[io.BytesIO]] = []
        for tile in tiles:
            url = self.get_tile_url(tile.z, tile.x, tile.y)
            print(f"Downloading {url}")
            tasks.append(asyncio.ensure_future(self._get_tile(url)))

        tile_images: List[io.BytesIO] = list(await asyncio.gather(*tasks))

        tileset_dimensions = get_tileset_dimensions(tiles, self.tile_size)

        mosaic = Image.new(
            "RGBA", (tileset_dimensions.total_cols, tileset_dimensions.total_rows)
        )

        x = 0
        y = 0
        for i, img in enumerate(tile_images):
            tile = Image.open(img)
            mosaic.paste(tile, (x * self.tile_size, y * self.tile_size))

            # Increment the row/col position for subsequent tiles
            if (i + 1) % tileset_dimensions.tile_rows == 0:
                y = 0
                x += 1
            else:
                y += 1

        raster_extent = RasterExtent(
            bbox=Bbox.from_tiles(tiles),
            cols=tileset_dimensions.total_cols,
            rows=tileset_dimensions.total_rows,
        )

        return PILRaster(raster_extent, mosaic)


async def get_tile_set(
    cql: Dict[str, Any],
    render_options: RenderOptions,
    settings: BaseExporterSettings,
    format: ExportFormats = ExportFormats.PNG,
    data_api_url_override: Optional[str] = None,
) -> Union[PILTileSet, GDALTileSet]:
    """Gets a tile set for the given CQL query and render options."""

    # Get the TileSet
    if format == ExportFormats.PNG:
        return await PILTileSet.create(
            cql, render_options, settings, data_api_url_override
        )
    else:
        return await GDALTileSet.create(
            cql, render_options, settings, data_api_url_override
        )
