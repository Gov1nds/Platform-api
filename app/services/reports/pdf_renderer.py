"""PDF report renderer using WeasyPrint (Blueprint §15, C27)."""
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "reports"

def render_pdf(report_type: str, data: dict) -> bytes:
    try:
        from weasyprint import HTML, CSS
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
        try:
            tpl = env.get_template(f"{report_type}.html")
        except Exception:
            tpl = env.from_string(_DEFAULT_TEMPLATE)
        html = tpl.render(**data, org_name=data.get("organization_name"),
                          generated_at=data.get("generated_at"))
        css_path = TEMPLATE_DIR / "style.css"
        stylesheets = [CSS(filename=str(css_path))] if css_path.exists() else []
        return HTML(string=html).write_pdf(stylesheets=stylesheets)
    except ImportError:
        logger.warning("WeasyPrint not available, returning placeholder PDF")
        return b"%PDF-1.4 placeholder"

_DEFAULT_TEMPLATE = """<!DOCTYPE html><html><body>
<h1>{{ report_type | default('Report') }}</h1>
<p>Organization: {{ org_name }}</p>
<p>Generated: {{ generated_at }}</p>
{% if ai_insight %}<p><strong>AI Insight:</strong> {{ ai_insight }}</p>{% endif %}
<table><thead><tr>{% for h in columns %}<th>{{ h }}</th>{% endfor %}</tr></thead>
<tbody>{% for row in rows %}<tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>
</body></html>"""
