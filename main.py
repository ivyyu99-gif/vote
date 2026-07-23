# ============================================================
# main.py
# 지역별 대통령선거(제21대, 2025-06-03) 개표결과 분석 대시보드
#
# ▶ 실행 방법
#     streamlit run main.py
#
# ▶ 배포 방법
#     이 폴더를 그대로 Streamlit Cloud에 올리고, 실행 파일로 main.py를 지정하면 됩니다.
#
# ▶ 이 앱이 하는 일
#     1) 데이터를 준비하는 방법은 4가지 중에서 고를 수 있습니다.
#        - GitHub CSV 자동 연동(추천): GitHub에 올려둔 CSV의 raw 주소만 넣으면 매번 자동으로 받아옴
#        - CSV 파일 업로드: data.go.kr에서 직접 받은 원본 CSV를 그때그때 올림
#        - 오픈API 실시간 연동: 공공데이터포털 오픈API 호출 (응답이 느리거나 실패할 수 있음)
#        - 데모 데이터: 아무것도 준비 안 됐을 때 화면 구조를 미리 보여주는 가상 데이터
#     2) 시도 / 시군구 / 읍면동 중 원하는 단위로 득표수를 합산합니다.
#     3) 주요 정당의 후보를 정당 고유 색으로 표시해서, 지도(시도 단위)와
#        막대그래프로 지역별 분포를 한눈에 보여줍니다.
#
# ※ 이 오픈API에는 선거인수·투표수·무효표 컬럼이 없어서 투표율은 계산할 수 없습니다.
# ============================================================

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# ------------------------------------------------------------
# 0. 기본 설정
# ------------------------------------------------------------
st.set_page_config(page_title="제21대 대통령선거 개표결과 분석", layout="wide")

# 선거 개표결과 오픈API 주소 (공공데이터포털 Swagger 문서에서 확인한 실제 값)
ELECTION_API_URL = (
    "https://api.odcloud.kr/api/15025528/v1/"
    "uddi:e95b84c2-53ef-4770-b028-d4b8210772da"
)

# 공공데이터포털에서 발급받은 "서비스키"는 코드에 직접 적지 않고,
# Streamlit의 secrets.toml 파일에 아래처럼 저장해서 불러옵니다.
#   DATA_GO_KR_SERVICE_KEY = "여기에 발급받은 키"
SERVICE_KEY = st.secrets.get("DATA_GO_KR_SERVICE_KEY", "")

# 원자료에 들어있는 컬럼 이름들 (오픈API 명세 기준)
COL_SIDO = "시도명"
COL_SIGUNGU = "구시군명"
COL_EUPMYEONDONG = "읍면동명"
COL_CANDIDATE = "후보자"
COL_VOTES = "득표수"

# 시도 단위 지도를 그릴 때 쓸 행정구역 경계 GeoJSON (공개 데이터, 시도명이 "name" 속성에 들어있음)
SIDO_GEOJSON_URL = (
    "https://raw.githubusercontent.com/southkorea/southkorea-maps/"
    "master/kostat/2013/json/skorea_provinces_geo_simple.json"
)

# ------------------------------------------------------------
# 0-1. 후보자 -> 정당 -> 색상 매핑
#      (제21대 대통령선거 실제 후보 기준. 여기 없는 이름은 자동으로 "기타/무소속" 회색 처리)
# ------------------------------------------------------------
CANDIDATE_PARTY = {
    "이재명": "더불어민주당",
    "김문수": "국민의힘",
    "이준석": "개혁신당",
    "권영국": "민주노동당",
    "황교안": "무소속",
    "송진호": "무소속",
    "구주와": "무소속",
}

PARTY_COLOR = {
    "더불어민주당": "#0050A2",
    "국민의힘": "#E4032E",
    "개혁신당": "#FF7210",
    "민주노동당": "#FFC224",
    "무소속": "#9AA0A6",
    "기타/무소속": "#9AA0A6",
}


def get_party(candidate_name: str) -> str:
    """후보자 이름으로 정당을 찾는다. 모르는 이름이면 '기타/무소속'으로 처리."""
    return CANDIDATE_PARTY.get(candidate_name, "기타/무소속")


def get_party_color(party_name: str) -> str:
    return PARTY_COLOR.get(party_name, "#9AA0A6")


# ------------------------------------------------------------
# 1. 오픈API 호출 (실패/오류 상황을 친절한 한국어 안내로 처리)
# ------------------------------------------------------------
@st.cache_data(ttl=60 * 60 * 24, show_spinner="선거 개표결과를 불러오는 중입니다...")
def fetch_election_raw(per_page: int = 1000):
    """
    선거 개표결과 오픈API를 호출해서 원자료(투표구 x 후보자 단위)를 모두 받아온다.
    반환값: (성공 시 DataFrame, 실패 시 None), (실패했을 때 보여줄 안내 메시지, 성공하면 None)
    """
    headers = {"Authorization": f"Infuser {SERVICE_KEY}"}
    rows = []
    page = 1

    while True:
        try:
            # timeout=15 : 15초 안에 응답이 없으면 포기하고 예외를 발생시킴
            response = requests.get(
                ELECTION_API_URL,
                params={"page": page, "perPage": per_page},
                headers=headers,
                timeout=15,
            )
        except requests.exceptions.Timeout:
            return None, "⏱️ 서버 응답이 너무 늦어서 요청을 중단했어요. 잠시 후 다시 시도해주세요."
        except requests.exceptions.ConnectionError:
            return None, "🔌 인터넷 연결 또는 서버 상태를 확인해주세요. 개표결과 서버에 연결할 수 없었어요."
        except requests.exceptions.RequestException as e:
            return None, f"⚠️ 개표결과를 불러오는 중 알 수 없는 오류가 발생했어요. ({e})"

        # HTTP 상태 코드가 200(성공)이 아니면 여기서 멈춤
        if response.status_code == 401:
            return None, "🔑 서비스키가 올바르지 않아요. secrets.toml에 등록한 DATA_GO_KR_SERVICE_KEY 값을 다시 확인해주세요."
        if response.status_code != 200:
            return None, f"⚠️ 개표결과 서버가 오류를 반환했어요. (HTTP 상태코드: {response.status_code})"

        # 응답이 JSON이 아니거나 형식이 깨진 경우
        try:
            body = response.json()
        except ValueError:
            return None, "⚠️ 개표결과 서버 응답을 이해할 수 없는 형식이에요. (JSON이 아님)"

        # 공공데이터포털 API는 오류가 나면 200 응답이어도 body 안에 'faultInfo'를 담아 보낼 때가 있음
        if isinstance(body, dict) and "faultInfo" in body:
            fault = body["faultInfo"]
            reason = fault.get("reasonCode") or fault.get("errorCode") or "알 수 없음"
            message = fault.get("errorMsg") or fault.get("message") or ""
            return None, (
                f"⚠️ 개표결과 오픈API가 오류를 반환했어요. (사유: {reason}) {message}\n"
                "서비스키가 유효한지, 활용신청이 승인되었는지 확인해주세요."
            )

        data = body.get("data", [])
        rows.extend(data)

        total_count = body.get("totalCount", len(rows))
        if len(rows) >= total_count or not data:
            break  # 더 가져올 데이터가 없으면 반복을 멈춤
        page += 1

    if not rows:
        return None, "ℹ️ 개표결과 데이터가 비어 있어요. 잠시 후 다시 시도해주세요."

    return pd.DataFrame(rows), None  # 성공! 안내 메시지는 없음(None)


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def fetch_sido_geojson():
    """
    시도(광역자치단체) 경계 GeoJSON을 공개 저장소에서 받아온다.
    실패하면 (None, 안내메시지)를 돌려주고, 호출한 쪽에서는 지도 없이 막대그래프만 보여준다.
    """
    try:
        resp = requests.get(SIDO_GEOJSON_URL, timeout=15)
        resp.raise_for_status()
        return resp.json(), None
    except requests.exceptions.RequestException as e:
        return None, f"🗺️ 지도 경계 데이터를 불러오지 못했어요. 지도 대신 막대그래프로 보여드릴게요. ({e})"


# 원본 CSV에 있을 수 있는 "후보자 득표수가 아닌" 컬럼들 (이 목록에 없는 컬럼은 전부 후보자 이름으로 취급)
NON_CANDIDATE_COLUMNS = {
    COL_SIDO, COL_SIGUNGU, COL_EUPMYEONDONG, "투표구명",
    "선거인수", "투표수", "무효투표수", "기권수", "계",
}


def parse_election_csv(raw_bytes: bytes) -> tuple:
    """
    CSV 파일의 원본 바이트(byte)를 받아서, 후보자/득표수가 있는
    "세로형(long)" 표로 통일해서 돌려준다. (파일 업로드/URL 다운로드 둘 다 이 함수를 씀)

    data.go.kr에서 받는 원본 CSV는 보통 후보자 이름이 각각의 열로 나뉜
    "가로형(wide)" 표라서, 이 함수가 자동으로 세로형으로 바꿔준다.
    (이미 세로형이면 그대로 사용)

    반환값: (성공 시 DataFrame, 실패 시 None), (실패했을 때 보여줄 안내 메시지, 성공하면 None)
    """
    # 한국 공공데이터 CSV는 UTF-8 또는 EUC-KR(CP949)로 저장된 경우가 많아, 둘 다 시도해본다.
    df = None
    for encoding in ("utf-8-sig", "cp949"):
        try:
            df = pd.read_csv(pd.io.common.BytesIO(raw_bytes), encoding=encoding)
            break
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue

    if df is None:
        return None, "📄 CSV 파일을 읽을 수 없었어요. 파일 인코딩(UTF-8/EUC-KR)을 확인해주세요."

    if COL_SIDO not in df.columns:
        return None, (
            f"📄 CSV에서 '{COL_SIDO}' 컬럼을 찾지 못했어요. "
            "중앙선거관리위원회 개표결과 원본 CSV가 맞는지 확인해주세요."
        )

    # 이미 후보자/득표수 컬럼이 있는 세로형이면 그대로 사용
    if COL_CANDIDATE in df.columns and COL_VOTES in df.columns:
        return df, None

    # 가로형(후보자 이름이 열)이면 -> 세로형으로 녹여서(melt) 통일
    candidate_columns = [c for c in df.columns if c not in NON_CANDIDATE_COLUMNS]
    if not candidate_columns:
        return None, "📄 CSV에서 후보자 득표수 컬럼을 찾지 못했어요. 컬럼 이름을 확인해주세요."

    id_columns = [c for c in [COL_SIDO, COL_SIGUNGU, COL_EUPMYEONDONG] if c in df.columns]
    long_df = df.melt(
        id_vars=id_columns, value_vars=candidate_columns,
        var_name=COL_CANDIDATE, value_name=COL_VOTES,
    )
    return long_df, None


def load_csv_file(uploaded_file) -> tuple:
    """사용자가 화면에서 직접 올린 CSV 파일을 읽어온다."""
    return parse_election_csv(uploaded_file.read())


@st.cache_data(ttl=60 * 60, show_spinner="GitHub에 올려둔 CSV를 불러오는 중입니다...")
def fetch_csv_from_github(url: str) -> tuple:
    """
    GitHub 등에 올려둔 CSV 파일의 '원본(raw)' 주소를 받아서 자동으로 내려받는다.
    예: https://raw.githubusercontent.com/사용자이름/저장소이름/main/election.csv

    반환값: (성공 시 DataFrame, 실패 시 None), (실패했을 때 보여줄 안내 메시지, 성공하면 None)
    """
    if not url:
        return None, "🔗 GitHub raw CSV 주소가 비어 있어요. 사이드바에 주소를 입력해주세요."

    try:
        resp = requests.get(url, timeout=15)
    except requests.exceptions.Timeout:
        return None, "⏱️ GitHub에서 CSV를 받아오는 데 시간이 너무 오래 걸려서 중단했어요. 잠시 후 다시 시도해주세요."
    except requests.exceptions.ConnectionError:
        return None, "🔌 GitHub 주소에 연결할 수 없었어요. 인터넷 연결과 주소를 확인해주세요."
    except requests.exceptions.RequestException as e:
        return None, f"⚠️ CSV를 받아오는 중 알 수 없는 오류가 발생했어요. ({e})"

    if resp.status_code == 404:
        return None, "🔍 해당 주소에서 파일을 찾을 수 없어요(404). GitHub raw 주소가 정확한지 확인해주세요."
    if resp.status_code != 200:
        return None, f"⚠️ GitHub에서 오류를 반환했어요. (HTTP 상태코드: {resp.status_code})"

    return parse_election_csv(resp.content)


# ------------------------------------------------------------
# 2. 화면 테스트용 가짜(데모) 데이터
#    - 실제 후보 이름을 사용해서, API 연동 전에도 정당 색상 표시를 확인할 수 있게 함
# ------------------------------------------------------------
def load_mock_data() -> pd.DataFrame:
    sample = [
        ("서울특별시", "강남구", "역삼동", "이재명", 9000),
        ("서울특별시", "강남구", "역삼동", "김문수", 12000),
        ("서울특별시", "강남구", "역삼동", "이준석", 1800),
        ("서울특별시", "강남구", "역삼동", "권영국", 300),
        ("서울특별시", "노원구", "상계동", "이재명", 11000),
        ("서울특별시", "노원구", "상계동", "김문수", 8000),
        ("서울특별시", "노원구", "상계동", "이준석", 1200),
        ("서울특별시", "노원구", "상계동", "권영국", 400),
        ("경기도", "성남시", "분당동", "이재명", 15000),
        ("경기도", "성남시", "분당동", "김문수", 10000),
        ("경기도", "성남시", "분당동", "이준석", 2000),
        ("경기도", "성남시", "분당동", "권영국", 350),
        ("부산광역시", "해운대구", "우동", "이재명", 9500),
        ("부산광역시", "해운대구", "우동", "김문수", 12500),
        ("부산광역시", "해운대구", "우동", "이준석", 1800),
        ("부산광역시", "해운대구", "우동", "권영국", 300),
        ("전라남도", "순천시", "연향동", "이재명", 13000),
        ("전라남도", "순천시", "연향동", "김문수", 6000),
        ("전라남도", "순천시", "연향동", "이준석", 900),
        ("전라남도", "순천시", "연향동", "권영국", 250),
        ("대구광역시", "수성구", "범어동", "이재명", 7000),
        ("대구광역시", "수성구", "범어동", "김문수", 14000),
        ("대구광역시", "수성구", "범어동", "이준석", 1500),
        ("대구광역시", "수성구", "범어동", "권영국", 200),
    ]
    return pd.DataFrame(
        sample, columns=[COL_SIDO, COL_SIGUNGU, COL_EUPMYEONDONG, COL_CANDIDATE, COL_VOTES]
    )


# ------------------------------------------------------------
# 3. 지역 단위별 집계
# ------------------------------------------------------------
def add_region_column(df: pd.DataFrame, level: str) -> pd.DataFrame:
    """선택한 지역 단위(시도/시군구/읍면동)에 맞춰 'region' 컬럼을 만든다."""
    df = df.copy()
    if level == "시도":
        df["region"] = df[COL_SIDO].fillna("")
    elif level == "시군구":
        df["region"] = df[COL_SIDO].fillna("") + " " + df[COL_SIGUNGU].fillna("")
    else:  # 읍면동
        df["region"] = (
            df[COL_SIDO].fillna("") + " " + df[COL_SIGUNGU].fillna("") + " " + df[COL_EUPMYEONDONG].fillna("")
        )
    return df


def aggregate_votes(df: pd.DataFrame, level: str) -> pd.DataFrame:
    """
    지역 단위별로 후보자 득표수를 합산해서
    "지역 | 후보자별 득표수(여러 열) | 총 득표수 | 1위 후보 | 1위 정당 | 1위 득표율" 표를 만든다.
    """
    df = add_region_column(df, level)
    df[COL_VOTES] = pd.to_numeric(df[COL_VOTES], errors="coerce").fillna(0)

    # 지역 x 후보자 별 득표 합산
    long_table = df.groupby(["region", COL_CANDIDATE])[COL_VOTES].sum().reset_index()

    # 시도 단위일 때는 지도 매칭을 위해 원래 시도명도 따로 보관
    if level == "시도":
        sido_lookup = df.drop_duplicates("region").set_index("region")[COL_SIDO]

    # 보기 편하게 "후보자"를 열(컬럼)로 펼침 (region이 행, 후보자 이름이 열)
    wide_table = long_table.pivot(index="region", columns=COL_CANDIDATE, values=COL_VOTES).fillna(0)

    candidate_cols = wide_table.columns.tolist()
    wide_table["총 득표수"] = wide_table[candidate_cols].sum(axis=1)
    wide_table["1위 후보"] = wide_table[candidate_cols].idxmax(axis=1)
    wide_table["1위 득표율(%)"] = (
        wide_table[candidate_cols].max(axis=1) / wide_table["총 득표수"] * 100
    ).round(2)
    wide_table["1위 정당"] = wide_table["1위 후보"].map(get_party)
    wide_table["정당색"] = wide_table["1위 정당"].map(get_party_color)

    return wide_table.reset_index()


def national_candidate_totals(df: pd.DataFrame) -> pd.DataFrame:
    """전국 후보자별 총 득표수와 정당, 정당 색상."""
    df = df.copy()
    df[COL_VOTES] = pd.to_numeric(df[COL_VOTES], errors="coerce").fillna(0)
    totals = df.groupby(COL_CANDIDATE)[COL_VOTES].sum().sort_values(ascending=False).reset_index()
    totals = totals.rename(columns={COL_VOTES: "총 득표수"})
    totals["정당"] = totals[COL_CANDIDATE].map(get_party)
    return totals


# ------------------------------------------------------------
# 4. 사이드바
# ------------------------------------------------------------
st.sidebar.header("설정")
data_source = st.sidebar.radio(
    "데이터 소스",
    ["GitHub CSV 자동 연동", "CSV 파일 업로드", "오픈API 실시간 연동", "데모 데이터"],
    index=0,
    help="GitHub에 CSV를 올려두면 매번 파일을 다시 올릴 필요 없이 자동으로 최신 파일을 받아와요.",
)
level = st.sidebar.radio("지역 분석 단위", ["시도", "시군구", "읍면동"], index=1)

if data_source == "GitHub CSV 자동 연동":
    st.sidebar.caption(
        "GitHub 저장소에 CSV를 올린 뒤, 그 파일의 'Raw' 버튼을 눌러 나오는 주소를 붙여넣으세요.\n"
        "예: https://raw.githubusercontent.com/사용자이름/저장소이름/main/election.csv"
    )
    github_csv_url = st.sidebar.text_input(
        "GitHub raw CSV 주소",
        value=st.secrets.get("GITHUB_CSV_URL", ""),
    )

elif data_source == "CSV 파일 업로드":
    st.sidebar.markdown(
        "[중앙선거관리위원회_대통령선거 개표결과_20250603 다운로드 페이지]"
        "(https://www.data.go.kr/data/15025528/fileData.do)"
    )
    uploaded_file = st.sidebar.file_uploader("개표결과 CSV 파일 업로드", type=["csv"])

with st.sidebar.expander("정당 색상 안내"):
    for party, color in PARTY_COLOR.items():
        if party == "기타/무소속":
            continue
        st.markdown(
            f"<span style='display:inline-block;width:12px;height:12px;"
            f"background-color:{color};border-radius:2px;margin-right:6px;'></span>{party}",
            unsafe_allow_html=True,
        )

# --- 원자료 준비 ---
raw_df = None

if data_source == "GitHub CSV 자동 연동":
    raw_df, error_message = fetch_csv_from_github(github_csv_url)
    if error_message:
        st.error(error_message)
        st.info("대신 데모(가상) 데이터를 보여드릴게요.")
        raw_df = None

elif data_source == "CSV 파일 업로드":
    if uploaded_file is not None:
        raw_df, error_message = load_csv_file(uploaded_file)
        if error_message:
            st.error(error_message)
            st.info("대신 데모(가상) 데이터를 보여드릴게요.")
            raw_df = None
    else:
        st.info("왼쪽에서 CSV 파일을 올려주세요. 올리기 전까지는 데모(가상) 데이터를 보여드릴게요.")

elif data_source == "오픈API 실시간 연동":
    raw_df, error_message = fetch_election_raw()
    if error_message:
        st.error(error_message)
        st.info("대신 데모(가상) 데이터를 보여드릴게요.")
        raw_df = None

if raw_df is None or raw_df.empty:
    raw_df = load_mock_data()

# --- 집계 ---
region_table = aggregate_votes(raw_df, level)
national_totals = national_candidate_totals(raw_df)
candidate_cols = [
    c for c in region_table.columns
    if c not in ("region", "총 득표수", "1위 후보", "1위 정당", "1위 득표율(%)", "정당색")
]


# ------------------------------------------------------------
# 5. 화면 본문
# ------------------------------------------------------------
st.title("🗳️ 제21대 대통령선거 개표결과 분석")
st.caption(f"현재 지역 분석 단위: {level}  ·  후보는 정당 고유 색으로 표시됩니다.")

col1, col2, col3 = st.columns(3)
col1.metric("전국 총 득표수", f"{national_totals['총 득표수'].sum():,.0f} 표")
col2.metric("후보자 수", f"{len(national_totals)} 명")
col3.metric(f"{level} 개수", f"{len(region_table)} 곳")

tab1, tab2, tab3, tab4 = st.tabs(["전국 후보별 득표", "지역별 분포(지도)", "지역별 비교", "지역 상세 조회"])

with tab1:
    st.subheader("전국 후보별 총 득표수")
    fig = px.bar(
        national_totals,
        x=COL_CANDIDATE,
        y="총 득표수",
        color="정당",
        color_discrete_map=PARTY_COLOR,
        text="총 득표수",
    )
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(national_totals, use_container_width=True)

with tab2:
    st.subheader("지역별 1위 정당 분포 지도")
    if level != "시도":
        st.info("지도는 현재 '시도' 단위에서만 지원돼요. 사이드바에서 지역 분석 단위를 '시도'로 바꿔보세요.")
    geojson, geo_error = fetch_sido_geojson()
    if level == "시도" and geojson is not None:
        map_fig = px.choropleth(
            region_table,
            geojson=geojson,
            featureidkey="properties.name",
            locations="region",
            color="1위 정당",
            color_discrete_map=PARTY_COLOR,
            hover_data=["1위 후보", "1위 득표율(%)", "총 득표수"],
        )
        map_fig.update_geos(fitbounds="locations", visible=False)
        map_fig.update_layout(margin={"r": 0, "t": 0, "l": 0, "b": 0})
        st.plotly_chart(map_fig, use_container_width=True)
    elif level == "시도" and geo_error:
        st.warning(geo_error)

    # 지도가 안 그려지는 경우(시군구/읍면동 단위, 또는 지도 데이터 로드 실패)를 대비한 대체용 막대그래프
    st.subheader(f"{level}별 1위 정당 (막대그래프)")
    bar_fig = px.bar(
        region_table.sort_values("1위 득표율(%)", ascending=False),
        x="region",
        y="1위 득표율(%)",
        color="1위 정당",
        color_discrete_map=PARTY_COLOR,
        hover_data=["1위 후보", "총 득표수"],
    )
    bar_fig.update_layout(xaxis_title="", xaxis={"categoryorder": "total descending"})
    st.plotly_chart(bar_fig, use_container_width=True)

with tab3:
    st.subheader(f"{level}별 비교 표")
    sort_col = st.selectbox("정렬 기준", ["1위 득표율(%)", "총 득표수"])
    sorted_table = region_table.sort_values(sort_col, ascending=False)
    st.dataframe(sorted_table, use_container_width=True)

    # 표를 CSV 파일로 내려받을 수 있는 버튼
    csv_bytes = sorted_table.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "CSV로 내려받기", data=csv_bytes, file_name=f"election_by_{level}.csv", mime="text/csv"
    )

with tab4:
    st.subheader("지역 하나를 골라 후보자별 득표수 자세히 보기")
    region_options = region_table["region"].sort_values().tolist()
    picked = st.selectbox("지역 선택", region_options)

    row = region_table[region_table["region"] == picked].iloc[0]
    detail = row[candidate_cols].sort_values(ascending=False)
    detail_df = detail.reset_index()
    detail_df.columns = [COL_CANDIDATE, "득표수"]
    detail_df["정당"] = detail_df[COL_CANDIDATE].map(get_party)

    detail_fig = px.bar(
        detail_df, x=COL_CANDIDATE, y="득표수", color="정당", color_discrete_map=PARTY_COLOR, text="득표수"
    )
    st.plotly_chart(detail_fig, use_container_width=True)

    st.write(
        f"**1위 후보:** {row['1위 후보']} ({row['1위 정당']})  ·  "
        f"**득표율:** {row['1위 득표율(%)']}%  ·  **총 득표수:** {row['총 득표수']:,.0f}표"
    )

st.divider()
st.caption(
    "⚠️ 이 오픈API에는 선거인수·투표수·무효표 컬럼이 없어 투표율은 계산할 수 없습니다. "
    "지도는 공개 행정구역 경계 데이터를 사용하며, 여기 없는 후보 이름은 '기타/무소속'(회색)으로 표시됩니다. "
    "서비스키가 없거나 오류가 나면 데모(가상) 데이터로 자동 대체됩니다."
)
