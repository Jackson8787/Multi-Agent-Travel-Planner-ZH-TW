"""PDF itinerary generator using fpdf2 with Windows CJK font support."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fpdf import FPDF, FontFace

from travel_planner.domain.models import DayPlanState, PriceStatus, SlotType, TripSpec
from travel_planner.domain.pace import PaceLevel

# ── CJK font discovery ───────────────────────────────────────────────────────

_FONT_CANDIDATES: list[Path] = [
    Path("C:/Windows/Fonts/msjh.ttc"),         # Microsoft JhengHei — 繁體中文
    Path("C:/Windows/Fonts/msyh.ttc"),          # Microsoft YaHei   — 簡體中文
    Path("C:/Windows/Fonts/meiryo.ttc"),        # Meiryo             — 日文
    Path("C:/Windows/Fonts/NotoSansTC-VF.ttf"), # Noto Sans TC       — 備用
]


def _find_cjk_font() -> Path | None:
    """回傳第一個可用的 CJK 字型路徑；非 Windows 環境下傳回 None。"""
    for p in _FONT_CANDIDATES:
        if p.exists():
            return p
    return None


# ── 本地化標籤 ────────────────────────────────────────────────────────────────

_PACE_LABELS: dict[PaceLevel, str] = {
    PaceLevel.VERY_RELAXED: "非常悠閒",
    PaceLevel.RELAXED: "悠閒",
    PaceLevel.STANDARD: "一般",
    PaceLevel.INTENSIVE: "密集",
}

_SLOT_LABELS: dict[SlotType, str] = {
    SlotType.ATTRACTION: "景點",
    SlotType.LUNCH_PLACEHOLDER: "午餐",
    SlotType.DINNER_PLACEHOLDER: "晚餐",
    SlotType.FREE_TIME: "自由",
    SlotType.LUNCH: "午餐",
    SlotType.DINNER: "晚餐",
}

_LINE_H = 7   # mm — 標準行高
_LMARGIN = "LMARGIN"
_NEXT = "NEXT"

# 時刻表欄寬（mm），合計 = A4 可列印寬 190mm
# 時間:26  類型:15  地點:65  備註:84
_COL_WIDTHS = (26, 15, 65, 84)

# FontFace 樣式（在 _draw_timeslot_table 中使用）
_STYLE_HDR = FontFace(fill_color=(225, 232, 248))
_STYLE_ODD = FontFace(fill_color=(245, 247, 252))
_STYLE_EVN = FontFace(fill_color=(255, 255, 255))


# ── PDF 子類 ──────────────────────────────────────────────────────────────────

class _ItineraryPDF(FPDF):
    """帶有 CJK 字型與版面輔助方法的 fpdf2 PDF。

    注意：所有 multi_cell 呼叫皆明確傳入 new_x="LMARGIN"，
    避免 fpdf2 ≥ 2.8 在渲染後將游標停在儲存格右側的新預設行為。
    """

    def __init__(self, font_path: Path | None) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        if font_path is not None:
            self.add_font("CJK", "", str(font_path))
            self._fn = "CJK"
        else:
            self._fn = "Helvetica"
        self.set_auto_page_break(auto=True, margin=15)

    # ── fpdf2 鉤子 ────────────────────────────────────────────────────────────

    def header(self) -> None:  # noqa: D102
        pass  # 章節標題以內文方式繪製

    def footer(self) -> None:  # noqa: D102
        self.set_y(-12)
        self.set_font(self._fn, size=8)
        self.set_text_color(160, 160, 160)
        self.cell(0, 5, f"第 {self.page_no()} 頁", align="C", new_x=_LMARGIN, new_y=_NEXT)
        self.set_text_color(0, 0, 0)

    # ── 版面輔助 ──────────────────────────────────────────────────────────────

    def h1(self, text: str) -> None:
        self.set_x(self.l_margin)
        self.set_font(self._fn, size=20)
        self.multi_cell(0, 12, text, new_x=_LMARGIN, new_y=_NEXT)
        self.ln(2)

    def h2(self, text: str) -> None:
        self.set_x(self.l_margin)
        self.set_font(self._fn, size=13)
        self.multi_cell(0, 9, text, new_x=_LMARGIN, new_y=_NEXT)
        self.ln(1)

    def body(self, text: str, size: int = 10) -> None:
        self.set_x(self.l_margin)
        self.set_font(self._fn, size=size)
        self.multi_cell(0, _LINE_H, text, new_x=_LMARGIN, new_y=_NEXT)

    def kv(self, key: str, value: str, key_w: float = 42) -> None:
        """固定寬度索引鍵 + 自動換行數值。"""
        self.set_x(self.l_margin)
        self.set_font(self._fn, size=10)
        self.cell(key_w, _LINE_H, key)
        val_w = self.w - self.r_margin - self.get_x()
        self.multi_cell(val_w, _LINE_H, value, new_x=_LMARGIN, new_y=_NEXT)

    def rule(self, thickness: float = 0.3) -> None:
        """在目前 Y 位置繪製全寬橫線。"""
        self.set_line_width(thickness)
        y = self.get_y()
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.ln(3)

    def blank_field(self, label: str) -> None:
        """帶標籤的空白填寫欄位（底線形式）。"""
        self.set_x(self.l_margin)
        self.set_font(self._fn, size=9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, label, new_x=_LMARGIN, new_y=_NEXT)
        self.set_text_color(0, 0, 0)
        y = self.get_y()
        self.line(self.l_margin, y + 4, self.w - self.r_margin, y + 4)
        self.ln(9)

    def ruled_lines(self, count: int = 14) -> None:
        """空白格線，供手寫備註使用。"""
        for _ in range(count):
            y = self.get_y()
            self.line(self.l_margin, y + 6, self.w - self.r_margin, y + 6)
            self.ln(9)


# ── 主要公開 API ──────────────────────────────────────────────────────────────

def generate_itinerary_pdf(
    trip_spec: TripSpec,
    approved_days: list[DayPlanState],
) -> bytes:
    """產生完整旅遊行程 PDF 並回傳原始位元組。

    章節
    ----
    1. 旅程概要 — 目的地、預算、住宿、興趣、已確認費用。
    2. 每日行程 — 時間表（時間 / 類型 / 地點 / 備註，支援自動換行）。
    3. 旅行備註與緊急聯絡資訊 — 空白格線 + 填寫欄位。

    Args:
        trip_spec: 完整的旅程規格（目的地、預算、住宿……）。
        approved_days: 已核准的 DayPlanState 清單（依天次排序）。

    Returns:
        適合傳入 st.download_button(data=…) 的 bytes。
    """
    pdf = _ItineraryPDF(_find_cjk_font())
    fx = trip_spec.fx_snapshot

    # ── 第一節：旅程概要 ──────────────────────────────────────────────────────
    pdf.add_page()
    pdf.ln(6)
    pdf.h1(f"{trip_spec.destination}  旅遊行程表")
    pdf.rule(0.6)
    pdf.ln(2)

    pdf.h2("旅程概要")
    pdf.kv("目的地：", trip_spec.destination)
    pdf.kv("天數：", f"{trip_spec.total_days} 天")
    pdf.kv("預算上限：", f"{trip_spec.budget_amount:,.0f} {trip_spec.budget_currency}")
    pace_label = _PACE_LABELS.get(trip_spec.pace.level, str(trip_spec.pace.level))
    pdf.kv("步調：", pace_label)
    pdf.kv("住宿：", trip_spec.hotel.name if trip_spec.hotel else "—")
    if trip_spec.interests:
        pdf.kv("興趣標籤：", "、".join(trip_spec.interests))
    if trip_spec.traveler_type:
        pdf.kv("旅遊類型：", trip_spec.traveler_type)

    pdf.ln(3)
    pdf.rule()
    pdf.ln(2)

    # 費用概覽（需要匯率快照）
    if fx:
        all_prices = list(trip_spec.prices)
        for ds in approved_days:
            all_prices.extend(ds.prices)
        confirmed_total = sum(
            (p.amount_original or Decimal("0")) * fx.rate
            for p in all_prices
            if p.amount_original is not None and p.status is not PriceStatus.MISSING_PRICE
        )
        pdf.h2("費用概覽")
        pdf.kv("匯率基準：", f"1 {fx.base_currency} = {fx.rate} {fx.target_currency}")
        pdf.kv("已確認費用：", f"{confirmed_total:,.2f} {fx.target_currency}")
        pdf.ln(3)
        pdf.rule()
        pdf.ln(2)

    # 全程景點
    all_place_names = [p.name for ds in approved_days for p in ds.places]
    if all_place_names:
        pdf.h2("全程景點一覽")
        pdf.body("、".join(all_place_names))
        pdf.ln(3)

    # 住宿空白填寫欄
    pdf.ln(2)
    pdf.blank_field("住宿地址：")
    pdf.blank_field("訂房確認號：")
    pdf.blank_field("入住 / 退房日期：")

    # ── 第二節：每日行程 ──────────────────────────────────────────────────────
    for ds in approved_days:
        pdf.add_page()
        pdf.ln(3)

        city = ds.destination or trip_spec.destination
        pdf.h2(f"第 {ds.day} 天  |  {city}")
        pdf.rule(0.5)
        pdf.ln(2)

        if ds.timeslots:
            _draw_timeslot_table(pdf, ds)
        else:
            # 備用：無時間槽資料時以清單呈現
            if ds.places:
                pdf.h2("景點")
                for p in ds.places:
                    pdf.body(f"  {p.name}")
            if ds.meals:
                pdf.h2("餐廳")
                for m in ds.meals:
                    pdf.body(f"  {m.name}")

        # 路線摘要列
        if ds.route:
            r = ds.route
            pdf.ln(2)
            pdf.set_x(pdf.l_margin)
            pdf.set_font(pdf._fn, size=9)
            pdf.set_text_color(90, 90, 90)
            pdf.multi_cell(
                0,
                6,
                (
                    f"移動時間合計：{r.total_required_transfer_minutes} 分鐘"
                    f"  |  最長單段：{r.max_single_transfer_minutes} 分鐘"
                    f"  |  總距離：{r.total_distance_km:.1f} 公里（步行 {r.walking_distance_km:.1f} 公里）"
                ),
                new_x=_LMARGIN,
                new_y=_NEXT,
            )
            pdf.set_text_color(0, 0, 0)

        # 當日費用
        if ds.prices and fx:
            day_cost = sum(
                (p.amount_original or Decimal("0")) * fx.rate
                for p in ds.prices
                if p.amount_original is not None and p.status is not PriceStatus.MISSING_PRICE
            )
            pdf.set_x(pdf.l_margin)
            pdf.set_font(pdf._fn, size=9)
            pdf.set_text_color(90, 90, 90)
            pdf.multi_cell(
                0,
                _LINE_H,
                f"當日已確認費用：{day_cost:,.2f} {fx.target_currency}",
                new_x=_LMARGIN,
                new_y=_NEXT,
            )
            pdf.set_text_color(0, 0, 0)

    # ── 第三節：旅行備註與緊急聯絡 ────────────────────────────────────────────
    pdf.add_page()
    pdf.ln(3)
    pdf.h2("旅行備註")
    pdf.rule(0.5)
    pdf.ln(2)
    pdf.ruled_lines(14)

    pdf.ln(3)
    pdf.h2("緊急聯絡資訊")
    pdf.rule(0.5)
    pdf.ln(2)
    pdf.blank_field("緊急聯絡人姓名 / 電話：")
    pdf.blank_field("台灣駐外館處電話：")
    pdf.blank_field("旅遊平安險保單號碼：")
    pdf.blank_field("當地救護 / 報警電話：")
    pdf.blank_field("信用卡掛失專線：")

    # fpdf2 ≥ 2.8 回傳 bytearray；st.download_button 需要 bytes。
    return bytes(pdf.output())


# ── 時刻表繪製 ────────────────────────────────────────────────────────────────

def _draw_timeslot_table(pdf: _ItineraryPDF, ds: DayPlanState) -> None:
    """使用 fpdf2 原生 table() API 繪製每日時刻表。

    各欄寬（mm）：時間 26 | 類型 15 | 地點 65 | 備註 84（合計 190mm）。
    備註欄使用自動換行，文字不再截斷或溢出。
    """
    pdf.set_font(pdf._fn, size=9)
    pdf.set_x(pdf.l_margin)

    with pdf.table(
        borders_layout="ALL",
        col_widths=_COL_WIDTHS,
        line_height=6,
        first_row_as_headings=False,
    ) as table:
        # 標題列
        hdr = table.row()
        for label in ("時間", "類型", "地點", "備註"):
            hdr.cell(label, style=_STYLE_HDR)

        # 資料列（隔行著色）
        for i, slot in enumerate(ds.timeslots):
            style = _STYLE_ODD if i % 2 == 1 else _STYLE_EVN
            row = table.row()
            row.cell(f"{slot.start_time}-{slot.end_time}", style=style)
            row.cell(_SLOT_LABELS.get(slot.slot_type, str(slot.slot_type)), style=style)
            row.cell(slot.location_name, style=style)
            row.cell(slot.notes, style=style)
