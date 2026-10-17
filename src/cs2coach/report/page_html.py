"""将报告页数据嵌入可离线打开的 HTML。"""

from importlib.resources import files

from cs2coach.report.page_data import ReportPage


def render_page(page: ReportPage) -> str:
    """只替换 JSON 占位，文字的页面转义由模板负责。"""
    template = files("cs2coach.report").joinpath("page_template.html").read_text(encoding="utf-8")
    data = page.model_dump_json().replace("</", "<\\/")
    return template.replace("__PAGE_DATA__", data)
