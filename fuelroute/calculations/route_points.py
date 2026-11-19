"""Kakao 상세 도로 거리·기하를 검증하고 안전 구간의 검색점을 보간한다. 통신은 하지 않는다."""

from dataclasses import dataclass
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field
from pyproj import Geod

from fuelroute.schemas.coordinates import Coordinate
from fuelroute.schemas.routes import RoutePoint

GEOD = Geod(ellps="WGS84")


class _Metrics(BaseModel):
    """Kakao가 제공한 도로·구간의 거리(m)와 시간(s). 정수만 허용한다."""

    distance: int = Field(ge=0, strict=True)
    duration: int = Field(ge=0, strict=True)


class _Road(BaseModel):
    """한 도로의 보고 거리와 경도·위도가 번갈아 나오는 상세 좌표."""

    model_config = ConfigDict(allow_inf_nan=False)
    distance: int = Field(ge=0, strict=True)
    vertexes: list[float] = Field(min_length=4)


class _Section(_Metrics):
    """출발지·경유지·목적지 사이 한 구간의 상세 도로 목록."""

    roads: list[_Road] = Field(min_length=1)


class _Route(BaseModel):
    """첫 번째 정상 경로의 요약과 순서가 보존된 구간들."""

    summary: _Metrics
    sections: list[_Section] = Field(min_length=1)


@dataclass(frozen=True)
class _Segment:
    """한 도로 내 인접 vertex 사이의 지오데식과 배분된 누적 도로 거리."""

    start: Coordinate
    end: Coordinate
    start_m: float
    end_m: float
    azimuth: float
    geodesic_m: float


@dataclass(frozen=True)
class RoutePath:
    """도로별 거리를 유지한 검색점 계산용 경로. 응답 원본이나 인증정보는 보관하지 않는다."""

    distance_m: float
    duration_s: int
    segments: tuple[_Segment, ...]

    def point_at_distance(self, distance_m: float) -> RoutePoint:
        """출발 후 distance_m 위치를 도로 내부의 인접 좌표 사이에서 보간한다.

        build_route_path로 검증한 경로에서 호출한다. 입력은 0~경로 총거리의 유한한
        m 값이며 범위 밖은 ValueError다. 도로의 보고 거리를 지오데식 길이 비율로
        배분한 축에서 위치를 찾고 WGS84 타원체 위를 보간한다. 도로 사이의 빈 공간을
        연결하지 않는다. 반환 경도·위도는 Opinet 검색점이며 도달 가능 판정값이 아니다.
        예: point_at_distance(80000)는 출발 후 약 80km 검색점이다.
        HTTP·DB·LLM·재시도 부작용은 없다. 도로 내 세부 거리 배분은 근사다.
        """
        if not isfinite(distance_m) or not 0 <= distance_m <= self.distance_m:
            raise ValueError("검색 거리는 0~전체 경로 거리의 유한값(m)이어야 합니다.")
        if distance_m == self.distance_m:
            point = self.segments[-1].end
        else:
            segment = next(item for item in self.segments if item.end_m > distance_m)
            fraction = (distance_m - segment.start_m) / (segment.end_m - segment.start_m)
            longitude, latitude, _ = GEOD.fwd(
                segment.start.longitude,
                segment.start.latitude,
                segment.azimuth,
                segment.geodesic_m * fraction,
            )
            point = Coordinate(longitude=longitude, latitude=latitude)
        return RoutePoint(**point.model_dump(), route_distance_m=distance_m)


def build_route_path(data: dict) -> RoutePath:
    """probe_route의 성공 data를 검증해 도로 누적거리 기반 검색점 계산 경로로 바꾼다.

    입력은 distance_m/duration_s와 원본 response.routes[0]을 포함해야 한다.
    도로 거리 합=구간 거리, 구간 거리 합=요약 거리와 응답의 단위를 확인한다.
    vertexes는 경도·위도 쌍이며 각 도로의 지오데식 길이 비율로 그 도로의 보고 거리를
    배분한다. 중복 좌표는 건너뛰고 도로 연결 오차 5m 초과·양수 거리의 빈 기하·
    합계 불일치·잘못된 좌표는 ValueError로 거절한다. 0m 경로도 거절한다.
    반환 RoutePath.point_at_distance로 안전 구간 내 검색점을 뽑는다. 외부 호출·
    파일 저장·추가 경로 요청은 없다. 예: build_route_path(result.data).
    지오데식 총길이로 자동차 경로 총거리를 대체하지 않는다.
    """
    try:
        raw = data["response"]["routes"][0]
        if raw.get("result_code") != 0:
            raise ValueError("정상 경로가 아님")
        route = _Route.model_validate(raw)
        if (
            route.summary.distance <= 0
            or route.summary.distance != data["distance_m"]
            or route.summary.duration != data["duration_s"]
            or sum(section.distance for section in route.sections) != route.summary.distance
        ):
            raise ValueError("경로 요약·구간 합계 불일치")
        segments = []
        accumulated = 0.0
        previous = None
        for section in route.sections:
            if sum(road.distance for road in section.roads) != section.distance:
                raise ValueError("도로 거리와 구간 거리 불일치")
            for road in section.roads:
                if len(road.vertexes) % 2:
                    raise ValueError("홀수 개 좌표")
                points = [
                    Coordinate(longitude=x, latitude=y)
                    for x, y in zip(road.vertexes[::2], road.vertexes[1::2])
                ]
                if (
                    previous is not None
                    and GEOD.inv(
                        previous.longitude,
                        previous.latitude,
                        points[0].longitude,
                        points[0].latitude,
                    )[2]
                    > 5
                ):
                    raise ValueError("도로 연결이 끊김")
                previous = points[-1]
                pairs = [
                    (
                        start,
                        end,
                        GEOD.inv(start.longitude, start.latitude, end.longitude, end.latitude),
                    )
                    for start, end in zip(points, points[1:])
                ]
                total_geometry = sum(values[2] for _, _, values in pairs)
                if road.distance > 0 and total_geometry == 0:
                    raise ValueError("양수 거리의 도로 기하가 비어 있음")
                if road.distance == 0 and total_geometry > 5:
                    raise ValueError("0m 도로에 긴 이동 기하가 있음")
                covered = 0.0
                for start, end, (azimuth, _, length) in pairs:
                    if not length or not road.distance:
                        continue
                    start_m = accumulated + road.distance * covered / total_geometry
                    covered += length
                    end_m = accumulated + road.distance * covered / total_geometry
                    segments.append(_Segment(start, end, start_m, end_m, azimuth, length))
                accumulated += road.distance
        if not segments:
            raise ValueError("보간할 경로 없음")
        return RoutePath(route.summary.distance, route.summary.duration, tuple(segments))
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError("상세 경로의 필수 구조를 확인하세요.") from exc


def generate_search_points(
    path: RoutePath, safe_range_km: float, radius_m: int = 5000, max_points: int = 3
) -> list[RoutePoint]:
    """안전거리와 목적지 중 가까운 한계 안에서 최대 세 검색점을 뒤쪽부터 생성한다.

    path는 검증된 기본 경로다. 안전거리는 양수 유한 km, 반경은 1~5000m,
    max_points는 1~3의 정수다. 한계에서 반경의 1/5/10배를 뺀 누적거리(m)를
    0으로 제한하고 중복을 제거한다. 기본 반경이면 끝에서 5/25/50km 전이다.
    반환점은 안전 누적거리 이내지만 반경 내 모든 주유소가 실제 도달 가능한 것은
    아니다. 후보별 자동차 경로 검증이 필수다. 전체 경로의 완전 탐색은 아니다.
    잘못된 인자는 ValueError, 외부 호출·저장·자동 확장은 없다.
    예: 안전거리 144km이면 기본 검색점은 139/119/94km다.
    """
    if (
        not isfinite(safe_range_km)
        or safe_range_km <= 0
        or type(radius_m) is not int
        or not 1 <= radius_m <= 5000
        or type(max_points) is not int
        or not 1 <= max_points <= 3
    ):
        raise ValueError("안전거리·반경·검색점 개수를 확인하세요.")
    end = min(path.distance_m, safe_range_km * 1000)
    distances = dict.fromkeys(max(0.0, end - factor * radius_m) for factor in (1, 5, 10))
    return [path.point_at_distance(distance) for distance in list(distances)[:max_points]]
