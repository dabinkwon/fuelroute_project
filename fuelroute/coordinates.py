"""WGS84와 Opinet KATEC(TM128)를 변환한다. 직선거리는 도달 판정에 사용하지 않는다."""

from functools import lru_cache
from math import isfinite

from pyproj import CRS, Geod, Transformer
from pyproj.crs import ProjectedCRS
from pyproj.crs.coordinate_operation import TransverseMercatorConversion

from fuelroute.probes import Coordinate


@lru_cache(maxsize=2)
def _transformer(to_katec: bool) -> Transformer:
    """검증한 한국1985/TM128 투영과 WGS84 사이 변환기를 캐시해 반환한다.

    to_katec=True는 경도·위도→X/Y(m), False는 역변환이다. Naver 공식 TM128
    투영 매개변수와 EPSG:4162 datum을 사용한다. EPSG:5178/5179와 혼용하지 않는다.
    always_xy=True, allow_ballpark=False로 축 순서·근사 변환을 통제한다.
    외부 API·네트워크·DB 부작용은 없다. PROJ 변환 불가 시 예외를 그대로 발생시킨다.
    """
    katec = ProjectedCRS(
        name="Korean 1985 / KATEC TM128",
        geodetic_crs=CRS.from_epsg(4162),
        conversion=TransverseMercatorConversion(
            latitude_natural_origin=38,
            longitude_natural_origin=128,
            scale_factor_natural_origin=0.9999,
            false_easting=400000,
            false_northing=600000,
        ),
    )
    source, target = (4326, katec) if to_katec else (katec, 4326)
    return Transformer.from_crs(source, target, always_xy=True, allow_ballpark=False)


def wgs84_to_katec(longitude: float, latitude: float) -> tuple[float, float]:
    """WGS84 경도·위도(도)를 Opinet 검색용 KATEC X/Y(m)로 변환한다.

    유한한 경도 -180~180, 위도 -90~90을 먼저 검증한다. 결과는 (X,Y)이며
    예: 서울 부근 (126.9821773,37.5646865) → 약 (310277,551868)m다.
    잘못된 입력·변환 실패는 ValidationError/PROJ 예외이며 보정하지 않는다.
    외부 호출·저장은 없고 결과 좌표의 실제 주유소 존재 여부는 별도 API로 검증한다.
    """
    point = Coordinate(longitude=longitude, latitude=latitude)
    return _transformer(True).transform(point.longitude, point.latitude, errcheck=True)


def katec_to_wgs84(x: float, y: float) -> Coordinate:
    """Opinet KATEC X/Y(m)를 다음 경로 조회에 쓸 WGS84 좌표로 바꾼다.

    두 값은 양수·유한해야 하며 순서를 바꾸지 않는다. Coordinate의 longitude,
    latitude(도)를 반환한다. 예: (314871.8,544012) → 약 (127.03509,37.49434).
    잘못된 값은 ValueError, 변환 불가는 PROJ 예외다. HTTP·저장 부작용은 없다.
    원본 주유소 위치의 오차까지 보정하거나 차량 접근 가능성을 보장하지 않는다.
    """
    if not all(isfinite(value) and value > 0 for value in (x, y)):
        raise ValueError("KATEC X/Y는 양수·유한값이어야 합니다.")
    longitude, latitude = _transformer(False).transform(x, y, errcheck=True)
    return Coordinate(longitude=longitude, latitude=latitude)


def geodesic_distance_m(origin: Coordinate, destination: Coordinate) -> float:
    """두 WGS84 좌표 사이 타원체 최단거리(m)를 반환한다.

    검증된 Coordinate를 받아 Geod(WGS84)로 계산한다. 반경 필터·변환 검증용이며
    자동차 도달거리나 우회거리로 해석하지 않는다. 같은 점이면 0m다.
    외부 호출·저장·예외 변환은 없다. 예: 거리<=radius_m으로 보조 후보를 필터한다.
    """
    return Geod(ellps="WGS84").inv(
        origin.longitude, origin.latitude, destination.longitude, destination.latitude
    )[2]
