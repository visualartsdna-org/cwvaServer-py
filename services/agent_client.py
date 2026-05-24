"""Ask / AI agent client page — port of AgentClient.groovy."""

from util.html_template import head, TAIL

SAMPLE_QUESTIONS = [
    "how many paintings",
    "artworks from Korea",
    "cold press paintings",
    "what is gesso",
    "compare cold press vs hot press",
    "paintings from the last 3 years",
]

# Plain string — no f-string — so CSS braces don't need doubling.
_CSS = """<style>
    :root {
        --primary-color: #4a6fa5;
        --primary-hover: #3d5d8a;
        --background: #f5f7fa;
        --card-background: #ffffff;
        --text-color: #333;
        --text-muted: #666;
        --border-color: #ddd;
        --success-color: #28a745;
        --error-color: #dc3545;
    }

    * { box-sizing: border-box; }

    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
        background: var(--background);
        color: var(--text-color);
        margin: 0;
        padding: 20px;
        line-height: 1.6;
    }

    .container { max-width: 900px; margin: 0 auto; }

    header { text-align: center; margin-bottom: 30px; }
    header h1 { color: var(--primary-color); margin-bottom: 5px; }
    header p  { color: var(--text-muted); margin: 0; }

    .query-section {
        background: var(--card-background);
        border-radius: 12px;
        padding: 24px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        margin-bottom: 20px;
    }

    .input-group { display: flex; gap: 12px; }

    #queryInput {
        flex: 1;
        padding: 14px 18px;
        font-size: 16px;
        border: 2px solid var(--border-color);
        border-radius: 8px;
        outline: none;
        transition: border-color 0.2s;
    }
    #queryInput:focus       { border-color: var(--primary-color); }
    #queryInput::placeholder { color: #aaa; }

    button {
        padding: 14px 28px;
        font-size: 16px;
        font-weight: 600;
        background: var(--primary-color);
        color: white;
        border: none;
        border-radius: 8px;
        cursor: pointer;
        transition: background 0.2s;
    }
    button:hover    { background: var(--primary-hover); }
    button:disabled { background: #aaa; cursor: not-allowed; }

    .examples {
        margin-top: 16px;
        padding-top: 16px;
        border-top: 1px solid var(--border-color);
    }
    .examples-label { font-size: 13px; color: var(--text-muted); margin-bottom: 8px; }
    .example-chips  { display: flex; flex-wrap: wrap; gap: 8px; }

    .example-chip {
        padding: 6px 12px;
        font-size: 13px;
        background: var(--background);
        border: 1px solid var(--border-color);
        border-radius: 20px;
        cursor: pointer;
        transition: all 0.2s;
    }
    .example-chip:hover {
        background: var(--primary-color);
        color: white;
        border-color: var(--primary-color);
    }

    .response-section {
        background: var(--card-background);
        border-radius: 12px;
        padding: 24px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        display: none;
    }
    .response-section.visible { display: block; }

    .response-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 16px;
    }
    .response-header h3 { margin: 0; color: var(--primary-color); }
    .response-meta { font-size: 13px; color: var(--text-muted); }

    .response-text {
        font-size: 17px;
        line-height: 1.7;
        margin-bottom: 20px;
        padding: 16px;
        background: var(--background);
        border-radius: 8px;
    }

    .response-text a.artwork-link {
        color: var(--primary-color);
        text-decoration: none;
        border-bottom: 1px dotted var(--primary-color);
        transition: all 0.2s;
    }
    .response-text a.artwork-link:hover {
        color: var(--primary-hover);
        border-bottom-style: solid;
    }

    .response-text a.external-link {
        color: #6b7280;
        text-decoration: none;
        border-bottom: 1px dotted #6b7280;
    }
    .response-text a.external-link::after { content: " ^"; font-size: 0.75em; }
    .response-text a.external-link:hover  { color: var(--primary-color); }

    .details-toggle {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 10px 0;
        font-size: 14px;
        color: var(--text-muted);
        cursor: pointer;
        user-select: none;
    }
    .details-toggle:hover { color: var(--primary-color); }
    .details-toggle .arrow { transition: transform 0.2s; }
    .details-toggle.open .arrow { transform: rotate(90deg); }

    .details-content { display: none; margin-top: 12px; }
    .details-content.visible { display: block; }

    .detail-block { margin-bottom: 16px; }
    .detail-label {
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        color: var(--text-muted);
        margin-bottom: 6px;
    }

    .sparql-display {
        font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
        font-size: 13px;
        background: #1e1e1e;
        color: #d4d4d4;
        padding: 16px;
        border-radius: 8px;
        overflow-x: auto;
        white-space: pre-wrap;
    }

    .data-table { width: 100%; border-collapse: collapse; font-size: 14px; }
    .data-table th,
    .data-table td {
        padding: 10px 12px;
        text-align: left;
        border-bottom: 1px solid var(--border-color);
    }
    .data-table th { background: var(--background); font-weight: 600; color: var(--text-muted); }
    .data-table tr:hover td { background: var(--background); }

    .loading {
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 40px;
        color: var(--text-muted);
    }
    .spinner {
        width: 24px;
        height: 24px;
        border: 3px solid var(--border-color);
        border-top-color: var(--primary-color);
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
        margin-right: 12px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    .error {
        color: var(--error-color);
        padding: 16px;
        background: #fff5f5;
        border-radius: 8px;
        border: 1px solid #ffcccc;
    }

    @media (max-width: 600px) {
        .input-group { flex-direction: column; }
        button { width: 100%; }
    }
</style>
"""

# Plain string — JS template literals use ${...} which must not be f-string interpolated.
# Root-relative /agent avoids WSL2 absolute-URL issues with fetch.
_SCRIPT = """<script>
    const DEFAULT_AGENT_URL = '/agent';

    function getAgentUrl() {
        return DEFAULT_AGENT_URL;
    }

    function setQuery(text) {
        document.getElementById('queryInput').value = text;
        document.getElementById('queryInput').focus();
    }

    function showLoading() {
        const section = document.getElementById('responseSection');
        const content = document.getElementById('responseContent');
        section.classList.add('visible');
        content.innerHTML = `
            <div class="loading">
                <div class="spinner"></div>
                <span>Thinking...</span>
            </div>
        `;
    }

    function showError(message) {
        const section = document.getElementById('responseSection');
        const content = document.getElementById('responseContent');
        section.classList.add('visible');
        content.innerHTML = `<div class="error">&#10007; ${message}</div>`;
    }

    function showResponse(data) {
        const section = document.getElementById('responseSection');
        const content = document.getElementById('responseContent');
        section.classList.add('visible');

        if (data.screened) {
            content.innerHTML = `
                <div class="response-header"><h3>Response</h3></div>
                <div class="response-text" style="border-left: 3px solid var(--primary-color);">
                    ${escapeHtml(data.response)}
                </div>
            `;
            return;
        }

        let html = `
            <div class="response-header">
                <h3>Response</h3>
                <span class="response-meta">
                    ${data.row_count} result${data.row_count !== 1 ? 's' : ''}
                    &bull; ${data.elapsed_seconds?.toFixed(2) || '?'}s
                </span>
            </div>
            <div class="response-text">${sanitizeResponseHtml(data.response)}</div>
        `;

        html += `
            <div class="details-toggle" onclick="toggleDetails(this)">
                <span class="arrow">&#9658;</span>
                <span>Show technical details</span>
            </div>
            <div class="details-content">
        `;

        if (data.sparql) {
            html += `
                <div class="detail-block">
                    <div class="detail-label">Generated SPARQL</div>
                    <div class="sparql-display">${escapeHtml(data.sparql)}</div>
                </div>
            `;
        }

        if (data.data && data.data.length > 0) {
            html += `
                <div class="detail-block">
                    <div class="detail-label">Data (${data.data.length} rows)</div>
                    ${buildDataTable(data.data)}
                </div>
            `;
        }

        html += `</div>`;
        content.innerHTML = html;
    }

    function buildDataTable(data) {
        if (!data || data.length === 0) return '<p>No data</p>';
        const columns = Object.keys(data[0]);
        const maxRows = 10;

        let html = '<table class="data-table"><thead><tr>';
        columns.forEach(col => { html += `<th>${escapeHtml(col)}</th>`; });
        html += '</tr></thead><tbody>';

        data.slice(0, maxRows).forEach(row => {
            html += '<tr>';
            columns.forEach(col => {
                let value = row[col] || '';
                if (value.length > 50) value = value.substring(0, 50) + '...';
                html += `<td>${escapeHtml(value)}</td>`;
            });
            html += '</tr>';
        });

        html += '</tbody></table>';

        if (data.length > maxRows) {
            html += `<p style="color: var(--text-muted); font-size: 13px; margin-top: 8px;">
                Showing ${maxRows} of ${data.length} rows
            </p>`;
        }
        return html;
    }

    function toggleDetails(element) {
        element.classList.toggle('open');
        element.nextElementSibling.classList.toggle('visible');
    }

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function sanitizeResponseHtml(text) {
        if (!text) return '';
        const anchors = [];
        const placeholder = '___SAFE_ANCHOR_';
        const anchorPattern = /<a\\s+href="(https?:\\/\\/[^"]+)"\\s+target="_blank"\\s+class="(artwork-link|external-link)">([^<]+)<\\/a>/gi;

        let processed = text.replace(anchorPattern, (match, url, cssClass, linkText) => {
            if (!url.match(/^https?:\\/\\//i)) return linkText;
            const index = anchors.length;
            anchors.push({ url, cssClass, linkText });
            return placeholder + index + '___';
        });

        processed = escapeHtml(processed);

        anchors.forEach((anchor, index) => {
            const safeAnchor = `<a href="${anchor.url}" target="_blank" class="${anchor.cssClass}">${escapeHtml(anchor.linkText)}</a>`;
            processed = processed.replace(placeholder + index + '___', safeAnchor);
        });

        return processed;
    }

    async function submitQuery() {
        const input  = document.getElementById('queryInput');
        const button = document.getElementById('submitBtn');
        const query  = input.value.trim();

        if (!query) { input.focus(); return; }

        button.disabled = true;
        showLoading();

        try {
            const response = await fetch(`${getAgentUrl()}/query`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: query })
            });

            if (response.status === 429) {
                const data = await response.json();
                showError(`Too many queries. Please wait ${data.retry_after || 60} seconds.`);
                return;
            }

            if (!response.ok) throw new Error(`Server returned ${response.status}`);

            const data = await response.json();
            if (data.error && !data.response) {
                showError(data.error);
            } else {
                showResponse(data);
            }

        } catch (error) {
            console.error('Query error:', error);
            showError(`Could not connect to agent at ${getAgentUrl()}. Is the server running?`);
        } finally {
            button.disabled = false;
        }
    }

    document.getElementById('queryInput').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') submitQuery();
    });

    document.getElementById('queryInput').focus();
</script>
"""


def get(srv) -> str:
    cfg = srv.cfg
    host = cfg["host"]

    chips = "\n".join(
        f'                    <span class="example-chip" onclick="setQuery(\'{q}\')">{q}</span>'
        for q in SAMPLE_QUESTIONS
    )

    body = f"""
<div class="container">
    <header>
        <h1>Visual Art Query</h1>
        <p>Ask questions about the artwork collection in natural language</p>
    </header>

    <div class="query-section">
        <div class="input-group">
            <input
                type="text"
                id="queryInput"
                placeholder="Ask a question... (e.g., 'how many paintings from Korea')"
                autocomplete="off"
            >
            <button id="submitBtn" onclick="submitQuery()">Ask</button>
        </div>
        <div class="examples">
            <div class="examples-label">Try an example:</div>
            <div class="example-chips">
{chips}
            </div>
        </div>
    </div>

    <div class="response-section" id="responseSection">
        <div id="responseContent"></div>
    </div>
</div>
<h5>
    <a href="/html/AgentAbout.html">About Visual Art Query</a>
</h5>
"""

    return head(host, server=srv) + _CSS + body + _SCRIPT + TAIL
