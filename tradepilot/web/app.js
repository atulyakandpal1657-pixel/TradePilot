async function loadDashboard(tradeId) {
    try {
        const response = await fetch(`/api/trace/${tradeId}`);
        if (!response.ok) {
            throw new Error('Trace data not found');
        }
        const data = await response.json();
        render(data);
    } catch (e) {
        console.error(e);
        document.body.innerHTML = `<h2 style="color:red; text-align:center;">Error loading TradePilot data: ${e.message}</h2>`;
    }
}

function renderEvidenceAuditHtml(audit) {
    if (!audit) {
        return `
            <div class="audit-missing-alert">
                <strong>No evidence audit recorded.</strong>
                <p style="margin-top:4px;">Original evidence or audit report was not captured for this decision.</p>
            </div>
        `;
    }

    const statusBadgeClass = audit.overall_status === 'PASSED'
        ? 'supported'
        : (audit.overall_status === 'FAILED'
            ? 'unsupported'
            : (audit.overall_status === 'MISSING_EVIDENCE'
                ? 'missing-evidence'
                : 'inconclusive'));

    let alertHtml = '';
    if (!audit.evidence_available || audit.overall_status === 'MISSING_EVIDENCE') {
        alertHtml = `
            <div class="audit-missing-alert">
                <strong>⚠ Original Evidence Unavailable:</strong> MarketSnapshot was not stored or available. Claims cannot be verified against market data.
            </div>
        `;
    } else if (!audit.claims || audit.claims.length === 0) {
        alertHtml = `
            <div class="audit-missing-alert">
                <strong>⚠ Inconclusive:</strong> No verifiable factual claims were found in this proposal. Audit cannot pass without verifiable claims.
            </div>
        `;
    }

    const summaryBar = `
        <div class="audit-summary-bar">
            <div><span class="metric-label">Status:</span> <span class="badge ${statusBadgeClass}" style="font-weight:bold;">${audit.overall_status}</span></div>
            <div><span class="metric-label">Supported:</span> <strong style="color:var(--success);">${audit.supported_count || 0}</strong></div>
            <div><span class="metric-label">Unsupported:</span> <strong style="color:var(--danger);">${audit.unsupported_count || 0}</strong></div>
            <div><span class="metric-label">Unverifiable:</span> <strong style="color:var(--warning);">${audit.unverifiable_count || 0}</strong></div>
            ${audit.snapshot_timestamp ? `<div><span class="metric-label">Snapshot:</span> <span style="font-size:0.75rem; color:var(--text-secondary);">${new Date(audit.snapshot_timestamp).toLocaleDateString()}</span></div>` : ''}
        </div>
        <p style="font-size:0.85rem; color:var(--text-secondary); margin-bottom:12px;">${audit.summary}</p>
    `;

    const claimsHtml = (audit.claims || []).map(c => {
        const claimBadge = c.status === 'SUPPORTED' ? 'supported' : (c.status === 'UNSUPPORTED' ? 'unsupported' : 'unverifiable');
        const severityClass = `severity-${(c.severity || 'info').toLowerCase()}`;
        return `
            <div class="audit-claim-item">
                <div class="audit-claim-header">
                    <span class="audit-claim-title">${c.claim}</span>
                    <div class="audit-claim-meta">
                        <span class="badge ${claimBadge}">${c.status}</span>
                        <span class="severity-pill ${severityClass}">${c.severity}</span>
                    </div>
                </div>
                ${c.evidence_source ? `<div class="audit-claim-source">Source: ${c.evidence_source} ${c.observed_value !== null && c.observed_value !== undefined ? `| Observed: ${c.observed_value}` : ''}</div>` : ''}
                <div class="audit-claim-explanation">${c.explanation}</div>
            </div>
        `;
    }).join('');

    return `
        ${alertHtml}
        ${summaryBar}
        <div class="audit-claims-list">
            ${claimsHtml || '<p style="font-size:0.85rem; color:var(--text-secondary);">No individual claims audited.</p>'}
        </div>
    `;
}

function render(data) {
    // 1. MARKET SNAPSHOT
    const snapHtml = `
        <div class="metric"><span class="metric-label">Symbol:</span><span class="metric-value">${data.record.symbol}</span></div>
        <div class="metric"><span class="metric-label">Market Price:</span><span class="metric-value">$${(data.snapshot_price || 0).toFixed(2)}</span></div>
        <div class="box">
            <strong>Evidence</strong>
            <p>${data.evidence_text || 'None recorded'}</p>
        </div>
    `;
    document.getElementById('market-snapshot').innerHTML = snapHtml;

    // 2. AI STRATEGY
    const actionClass = data.record.action === 'BUY' ? 'text-success' : 'text-danger';
    const stratHtml = `
        <div class="metric"><span class="metric-label">Proposed Action:</span><span class="metric-value ${actionClass}">${data.record.action}</span></div>
        <div class="metric"><span class="metric-label">Confidence:</span><span class="metric-value">${(data.record.original_confidence * 100).toFixed(0)}%</span></div>
        <div class="box">
            <strong>Thesis</strong>
            <p>${data.thesis_text || '(not stored)'}</p>
        </div>
        <div class="box">
            <strong>Assumptions</strong>
            <ul>${(data.record.assumptions || []).map(a => `<li>${a}</li>`).join('')}</ul>
        </div>
        <div class="box">
            <strong>Invalidation Conditions</strong>
            <ul>${(data.record.invalidation_conditions || []).map(c => `<li>${c}</li>`).join('')}</ul>
        </div>
    `;
    document.getElementById('ai-strategy').innerHTML = stratHtml;

    // 3. RISK ENGINE
    const riskStatus = data.record.risk_decision === 'APPROVED' ? 'text-success' : 'text-danger';
    const riskHtml = `
        <div class="metric" style="margin-bottom: 15px;"><span class="metric-label">RiskEngine Verdict:</span><span class="metric-value ${riskStatus}" style="font-size: 1.1rem;">${data.record.risk_decision}</span></div>
        <p style="font-size: 0.8rem; color: var(--warning); margin-bottom: 10px; font-weight: bold;">AI cannot override these checks.</p>
        <div class="box" style="margin-bottom: 0;">
            <ul style="margin-bottom: 0;">
                <li>max_position_pct</li>
                <li>buying_power</li>
                <li>max_open_positions</li>
                <li>daily_loss_limit</li>
                <li>stop-loss validation</li>
            </ul>
        </div>
    `;
    document.getElementById('risk-engine').innerHTML = riskHtml;

    // 4 & 5. EXECUTION
    const filledQty = (data.live_filled_qty !== null && data.live_filled_qty !== undefined) ? data.live_filled_qty : data.record.filled_quantity;
    
    const execHtml = `
        <div class="box" style="margin-bottom: 15px;">
            <strong>Demo Safety & Sizing</strong>
            <div class="metric"><span class="metric-label">Human Confirmation:</span><span class="metric-value text-success">REQUIRED [y/N]</span></div>
            <div class="metric"><span class="metric-label">Demo Cap:</span><span class="metric-value">$${data.demo_cap.toFixed(2)}</span></div>
            <p style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 5px;">${data.demo_note}</p>
        </div>
        <div class="box" style="margin-bottom: 15px; border-left: 3px solid var(--accent);">
            <strong>Actual Alpaca Execution</strong>
            <div class="metric"><span class="metric-label">Client Order ID:</span><span class="metric-value uuid">${data.client_oid_live}</span></div>
            <div class="metric"><span class="metric-label">Alpaca Order ID:</span><span class="metric-value uuid">${data.alpaca_oid_live || 'Pending'}</span></div>
            <div style="margin: 10px 0; border-top: 1px solid var(--border); padding-top: 10px;">
                <div class="metric"><span class="metric-label">Status:</span><span class="metric-value" style="font-weight:bold; color:var(--text-primary);">${data.live_status || data.record.execution_status}</span></div>
                <div class="metric"><span class="metric-label">Submitted Qty:</span><span class="metric-value">${data.record.quantity}</span></div>
                <div class="metric"><span class="metric-label">Filled Qty:</span><span class="metric-value" style="font-weight:bold;">${filledQty}</span></div>
                <div class="metric"><span class="metric-label">Fill Price:</span><span class="metric-value">$${data.actual_fill_price !== null ? data.actual_fill_price.toFixed(2) : '(pending)'}</span></div>
            </div>
        </div>
        <div class="metric" style="background: rgba(255,255,255,0.05); padding: 10px; border-radius: 4px;">
            <span class="metric-label">Outcome:</span><span class="metric-value text-warning">${data.outcome_line}</span>
        </div>
    `;
    document.getElementById('execution-section').innerHTML = execHtml;

    // 5. EVIDENCE AUDIT (Step 10)
    const auditContainer = document.getElementById('evidence-audit-container');
    const badgeContainer = document.getElementById('evidence-audit-badge-container');
    if (auditContainer) {
        auditContainer.innerHTML = renderEvidenceAuditHtml(data.evidence_audit);
    }
    if (badgeContainer && data.evidence_audit) {
        const badgeClass = data.evidence_audit.overall_status === 'PASSED'
            ? 'supported'
            : (data.evidence_audit.overall_status === 'FAILED'
                ? 'unsupported'
                : (data.evidence_audit.overall_status === 'MISSING_EVIDENCE' ? 'missing-evidence' : 'inconclusive'));
        badgeContainer.innerHTML = `<span class="badge ${badgeClass}">${data.evidence_audit.overall_status}</span>`;
    } else if (badgeContainer) {
        badgeContainer.innerHTML = '';
    }

    // 6. DECISION TRACE TIMELINE
    const steps = [
        "Market Evidence", "AI Strategy", "Evidence Audit", "Confidence", "Thesis", 
        "Risk Engine", "Human Approval", "Alpaca Execution", 
        "Actual Fill", "Position Outcome", "Forensics", "Memory"
    ];
    let timelineHtml = '';
    steps.forEach((step, idx) => {
        timelineHtml += `<span class="timeline-step active">${step}</span>`;
        if (idx < steps.length - 1) {
            timelineHtml += `<span class="timeline-arrow">→</span>`;
        }
    });
    document.getElementById('decision-trace').innerHTML = timelineHtml;

    // 7. LEARNING LOOP
    let memoryHtml = '';
    if (data.record.exit_price === null) {
        memoryHtml = `
            <div class="box" style="border: 1px solid var(--warning);">
                <strong class="text-warning">Position open — forensics pending.</strong>
                <p>Forensics will analyze this trade once an exit is confirmed.</p>
            </div>
        `;
    } else {
        if (data.memories && data.memories.length > 0) {
            memoryHtml = data.memories.map(m => `
                <div class="box">
                    <strong>Lesson Learned</strong>
                    <p>${m.lesson}</p>
                    <div style="margin-top: 5px; font-size: 0.8rem;">
                        <span class="badge accent">Conf: ${(m.confidence * 100).toFixed(0)}%</span>
                        <span class="badge success">Evid: ${m.evidence_count}</span>
                    </div>
                </div>
            `).join('');
        } else {
            memoryHtml = '<p>No lessons extracted yet.</p>';
        }
    }
    
    document.getElementById('learning-loop').innerHTML = `
        <div style="display:flex; gap: 10px; align-items:center; margin-bottom: 15px; color: var(--text-secondary); font-size: 0.85rem;">
            <span>Trade Outcome</span> <span>→</span>
            <span>Forensics</span> <span>→</span>
            <span>Lesson</span> <span>→</span>
            <span>TradeMemory</span> <span>→</span>
            <span>Future Strategy</span>
        </div>
        ${memoryHtml}
    `;
}


document.addEventListener('DOMContentLoaded', async () => {
    await loadAccountSnapshot();
    await loadTradeJournal();
});

async function loadAccountSnapshot() {
    try {
        const res = await fetch('/api/portfolio');
        if (!res.ok) return;
        const data = await res.json();
        
        const html = `
            <div class="metric"><span class="metric-label">Account Equity:</span><span class="metric-value">$${data.account_equity.toFixed(2)}</span></div>
            <div class="metric"><span class="metric-label">Buying Power:</span><span class="metric-value">$${data.buying_power.toFixed(2)}</span></div>
            <div class="metric"><span class="metric-label">Cash:</span><span class="metric-value">$${data.cash.toFixed(2)}</span></div>
            <div class="metric"><span class="metric-label">Open Positions:</span><span class="metric-value">${data.number_of_open_positions}</span></div>
        `;
        document.getElementById('account-snapshot-content').innerHTML = html;
    } catch (e) {
        console.error('Failed to load portfolio', e);
    }
}

async function loadTradeJournal() {
    try {
        const res = await fetch('/api/trades');
        if (!res.ok) return;
        const data = await res.json();
        const tbody = document.getElementById('trade-journal-body');
        
        let html = '';
        data.trades.forEach(t => {
            const dateStr = t.created_at ? new Date(t.created_at).toLocaleString() : '—';
            const actionClass = t.action === 'BUY' ? 'text-success' : 'text-danger';
            
            // Format fill price correctly if it's not a number it shouldn't crash
            const fillPrice = t.average_fill_price ? `$${t.average_fill_price.toFixed(2)}` : '—';
            const execStatus = t.execution_status || '—';
            
            html += `
                <tr onclick="showTradeDetails('${t.trade_id}')">
                    <td>${dateStr}</td>
                    <td><strong>${t.symbol}</strong></td>
                    <td class="${actionClass}">${t.action}</td>
                    <td>${t.quantity}</td>
                    <td>${t.order_type}</td>
                    <td><span class="badge neutral">${t.lifecycle_status}</span></td>
                    <td><span class="badge ${execStatus === 'FILLED' ? 'success' : (execStatus === 'REJECTED' ? 'danger' : 'warning')}">${execStatus}</span></td>
                    <td>${t.filled_quantity}</td>
                    <td>${fillPrice}</td>
                </tr>
            `;
        });
        
        tbody.innerHTML = html;
    } catch (e) {
        console.error('Failed to load trades', e);
    }
}

async function showTradeDetails(tradeId) {
    try {
        const res = await fetch(`/api/trades/${tradeId}`);
        if (!res.ok) return;
        const t = await res.json();
        
        const content = document.getElementById('trade-details-content');
        content.innerHTML = `
            <div class="box">
                <strong>Trade</strong>
                <div class="metric"><span class="metric-label">Trade ID:</span><span class="metric-value uuid">${t.trade_id}</span></div>
                <div class="metric"><span class="metric-label">Thesis ID:</span><span class="metric-value uuid">${t.thesis_id}</span></div>
                <div class="metric"><span class="metric-label">Symbol:</span><span class="metric-value">${t.symbol}</span></div>
                <div class="metric"><span class="metric-label">Action:</span><span class="metric-value">${t.action}</span></div>
                <div class="metric"><span class="metric-label">Quantity:</span><span class="metric-value">${t.quantity}</span></div>
                <div class="metric"><span class="metric-label">Order Type:</span><span class="metric-value">${t.order_type}</span></div>
                <div class="metric"><span class="metric-label">Time In Force:</span><span class="metric-value">${t.time_in_force}</span></div>
            </div>
            
            <div class="box">
                <strong>Execution</strong>
                <div class="metric"><span class="metric-label">Status:</span><span class="metric-value">${t.execution_status || '—'}</span></div>
                <div class="metric"><span class="metric-label">Filled Qty:</span><span class="metric-value">${t.filled_quantity}</span></div>
                <div class="metric"><span class="metric-label">Avg Fill Price:</span><span class="metric-value">${t.average_fill_price ? `$${t.average_fill_price.toFixed(2)}` : '—'}</span></div>
                <div class="metric"><span class="metric-label">Client Order ID:</span><span class="metric-value uuid">${t.client_order_id || '—'}</span></div>
                <div class="metric"><span class="metric-label">Alpaca Order ID:</span><span class="metric-value uuid">${t.alpaca_order_id || '—'}</span></div>
            </div>

            <div class="box" style="margin-top:10px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                    <strong>Evidence Audit</strong>
                    ${t.evidence_audit ? `<span class="badge ${t.evidence_audit.overall_status === 'PASSED' ? 'supported' : (t.evidence_audit.overall_status === 'FAILED' ? 'unsupported' : 'inconclusive')}">${t.evidence_audit.overall_status}</span>` : ''}
                </div>
                ${renderEvidenceAuditHtml(t.evidence_audit)}
            </div>
        `;
        
        const btn = document.getElementById('view-trace-btn');
        btn.onclick = () => {
            document.getElementById('trade-details-modal').style.display = 'none';
            loadDashboard(t.trade_id);
            // Scroll to trace
            document.getElementById('decision-trace').scrollIntoView({behavior: 'smooth'});
        };
        
        document.getElementById('trade-details-modal').style.display = 'block';
    } catch (e) {
        console.error('Failed to load trade details', e);
    }
}


// ─────────────────────────────────────────────────────────────────────────────
// MARKET WATCH
// Fetches latest daily-bar market data from GET /api/market/{symbol}.
// Data is NOT real-time streaming — it reflects the most recent completed
// trading day available from Alpaca (IEX feed, daily resolution).
//
// Structure is intentionally modular so a future multi-symbol batch endpoint
// can replace fetchOneSymbol() without rewriting the render layer.
// ─────────────────────────────────────────────────────────────────────────────

const MW = (() => {
    const SYMBOLS = ['PLTR', 'NVDA', 'AAPL'];
    const REFRESH_MS = 30_000; // 30 seconds — conservative for daily bars

    // State
    let data = {};          // { symbol -> snapshot object | null | 'loading' }
    let aiData = {};        // { symbol -> ai analysis object | null | 'loading' | 'error message' }
    let selected = null;    // currently selected symbol string
    let refreshing = false; // overlap guard
    let timer = null;

    // ── Formatting helpers ────────────────────────────────────────────────────

    function fmtPrice(v) {
        return v != null ? `$${Number(v).toFixed(2)}` : '—';
    }

    function fmtPct(v) {
        if (v == null) return '—';
        const pct = (Number(v) * 100).toFixed(2);
        return `${Number(v) >= 0 ? '+' : ''}${pct}%`;
    }

    function fmtVol(v) {
        if (v == null) return '—';
        return Number(v).toLocaleString();
    }

    function fmtTimestamp(ts) {
        if (!ts) return '—';
        try {
            return new Date(ts).toLocaleString(undefined, {
                month: 'short', day: 'numeric', year: 'numeric',
                hour: '2-digit', minute: '2-digit', timeZoneName: 'short'
            });
        } catch {
            return String(ts);
        }
    }

    function changeClass(v) {
        if (v == null) return 'neutral';
        return Number(v) >= 0 ? 'pos' : 'neg';
    }

    // ── Data fetching ─────────────────────────────────────────────────────────

    /**
     * Fetch market data for a single symbol from the backend.
     * Returns the parsed JSON snapshot or throws on error.
     * Designed to be replaceable by a batch endpoint in the future.
     */
    async function fetchOneSymbol(symbol) {
        const res = await fetch(`/api/market/${encodeURIComponent(symbol)}`);
        if (!res.ok) {
            let detail = `HTTP ${res.status}`;
            try {
                const body = await res.json();
                if (body && body.detail) {
                    // Trim long server messages; never display raw tracebacks
                    detail = String(body.detail).slice(0, 200);
                }
            } catch { /* ignore JSON parse failure */ }
            throw new Error(detail);
        }
        const json = await res.json();
        if (!json || typeof json.current_price === 'undefined') {
            throw new Error('Malformed response from server');
        }
        return json;
    }

    async function analyzeSymbol(symbol) {
        if (aiData[symbol] === 'loading') return;
        aiData[symbol] = 'loading';
        renderDetailPanel(selected);

        try {
            const res = await fetch(`/api/analyze/${encodeURIComponent(symbol)}`, {
                method: 'POST'
            });
            if (!res.ok) {
                let detail = `HTTP ${res.status}`;
                try {
                    const body = await res.json();
                    if (body && body.detail) {
                        detail = String(body.detail).slice(0, 200);
                    }
                } catch { }
                throw new Error(detail);
            }
            const json = await res.json();
            aiData[symbol] = json;
        } catch (err) {
            console.warn(`[MarketWatch] AI Analysis failed for ${symbol}:`, err.message);
            aiData[symbol] = err.message;
        }
        
        if (selected === symbol) {
            renderDetailPanel(selected);
        }
    }

    async function evaluateRisk(symbol) {
        if (!aiData[symbol] || typeof aiData[symbol] === 'string' || aiData[symbol] === 'loading') return;
        
        const snap = data[symbol];
        if (!snap || snap === 'loading' || snap === null) return;
        
        const btn = document.getElementById(`btn-evaluate-risk-${symbol}`);
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Evaluating...';
        }

        try {
            const res = await fetch(`/api/risk/${encodeURIComponent(symbol)}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    snapshot_timestamp: snap.timestamp,
                    strategy_proposal: aiData[symbol].analysis
                })
            });

            if (res.status === 409) {
                // Stale analysis
                aiData[symbol].risk = { error: true, status: 409, message: "Stale analysis: Market data has changed. Please re-analyze first." };
            } else if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                aiData[symbol].risk = { error: true, status: res.status, message: body.detail || `HTTP ${res.status}` };
            } else {
                const json = await res.json();
                aiData[symbol].risk = json;
            }
        } catch (err) {
            aiData[symbol].risk = { error: true, status: 500, message: err.message };
        }

        if (selected === symbol) {
            renderDetailPanel(selected);
        }
    }

    async function approveTrade(tradeId, sym) {
        const btn = document.getElementById(`btn-approve-${tradeId}`);
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Approving…';
        }
        try {
            const res = await fetch(`/api/approve/${encodeURIComponent(tradeId)}`, {
                method: 'POST'
            });
            const body = await res.json().catch(() => ({}));
            if (res.status === 409) {
                aiData[sym].approval = { error: true, status: 409, message: body.detail || 'Already approved or stale.' };
            } else if (res.status === 400) {
                aiData[sym].approval = { error: true, status: 400, message: body.detail || 'Risk conditions changed. Re-analyze required.' };
            } else if (!res.ok) {
                aiData[sym].approval = { error: true, status: res.status, message: body.detail || `HTTP ${res.status}` };
            } else {
                aiData[sym].approval = { success: true, data: body };
            }
        } catch (err) {
            aiData[sym].approval = { error: true, status: 500, message: err.message };
        }
        if (selected === sym) {
            renderDetailPanel(selected);
        }
    }

    async function executeTrade(tradeId, sym) {
        // POST /api/execute/{trade_id} — no trade terms in the body.
        // The backend uses the exact stored HUMAN_APPROVED proposal.
        const btn = document.getElementById(`btn-execute-${tradeId}`);
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Submitting to Alpaca…';
        }
        try {
            const res = await fetch(`/api/execute/${encodeURIComponent(tradeId)}`, {
                method: 'POST'
                // Intentionally no body — trade terms must not be sent by frontend
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) {
                aiData[sym].execution = {
                    error: true,
                    status: res.status,
                    message: body.detail || `HTTP ${res.status}`
                };
            } else {
                aiData[sym].execution = { success: true, data: body };
            }
        } catch (err) {
            aiData[sym].execution = { error: true, status: 500, message: err.message };
        }
        if (selected === sym) {
            renderDetailPanel(selected);
        }
    }

    /**
     * Refresh all symbols. Sets per-symbol state to null on failure so the
     * UI shows "Data unavailable" for that ticker without destroying others.
     * Preserves the currently selected symbol across refreshes.
     */
    async function refresh() {
        if (refreshing) return;
        refreshing = true;
        setRefreshButton(true);

        // Initialise symbols to 'loading' only if they have no data yet
        for (const sym of SYMBOLS) {
            if (data[sym] === undefined) {
                data[sym] = 'loading';
            }
        }
        renderTickerRow();

        // Fetch all symbols concurrently; failures are isolated
        await Promise.all(SYMBOLS.map(async (sym) => {
            try {
                data[sym] = await fetchOneSymbol(sym);
            } catch (err) {
                console.warn(`[MarketWatch] Failed to load ${sym}:`, err.message);
                data[sym] = null; // explicit unavailable state
            }
        }));

        renderTickerRow();

        // Update the "last fetched" timestamp (distinct from per-symbol data timestamp)
        const now = new Date().toLocaleTimeString(undefined, {
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        });
        const el = document.getElementById('market-last-updated');
        if (el) el.textContent = `Updated: ${now}`;

        // Re-render detail panel if a symbol is still selected
        if (selected && data[selected] && data[selected] !== 'loading') {
            renderDetailPanel(selected);
        }

        refreshing = false;
        setRefreshButton(false);
    }

    function setRefreshButton(busy) {
        const btn = document.getElementById('market-refresh-btn');
        if (!btn) return;
        btn.disabled = busy;
        btn.textContent = busy ? '↻ Refreshing…' : '↻ Refresh';
    }

    // ── Rendering ─────────────────────────────────────────────────────────────

    function renderTickerRow() {
        const row = document.getElementById('market-ticker-row');
        if (!row) return;

        row.innerHTML = SYMBOLS.map(sym => {
            const snap = data[sym];
            const isSelected = selected === sym;
            const selectedClass = isSelected ? ' selected' : '';

            if (snap === 'loading' || snap === undefined) {
                return `
                  <div class="market-ticker-card${selectedClass}"
                       id="ticker-card-${sym}"
                       role="button" aria-label="${sym} market data"
                       onclick="MW.select('${sym}')">
                    <div class="ticker-symbol">${sym}</div>
                    <div class="ticker-loading">Loading market data…</div>
                  </div>`;
            }

            if (snap === null) {
                return `
                  <div class="market-ticker-card unavailable${selectedClass}"
                       id="ticker-card-${sym}"
                       role="status" aria-label="${sym} data unavailable">
                    <div class="ticker-symbol">${sym}</div>
                    <div class="ticker-unavailable">Data unavailable</div>
                  </div>`;
            }

            const pctClass = changeClass(snap.price_change_pct);
            return `
              <div class="market-ticker-card${selectedClass}"
                   id="ticker-card-${sym}"
                   role="button" aria-label="Select ${sym}"
                   onclick="MW.select('${sym}')">
                <div class="ticker-symbol">${sym}</div>
                <div class="ticker-price">${fmtPrice(snap.current_price)}</div>
                <div class="ticker-change ${pctClass}">${fmtPct(snap.price_change_pct)}</div>
                <div class="ticker-indicators">
                  <span>SMA20 &nbsp;${fmtPrice(snap.sma_20)}</span>
                  <span>SMA50 &nbsp;${fmtPrice(snap.sma_50)}</span>
                  <span>Vol &nbsp;&nbsp;&nbsp;${Number(snap.volatility).toFixed(4)}</span>
                </div>
              </div>`;
        }).join('');
    }

    /**
     * Render the detail panel for the selected symbol.
     * Shows two distinct timestamps:
     *   • Data timestamp: the date the bar represents (from the API snapshot)
     *   • "Fetched" is conveyed by the global "Updated: HH:MM:SS" label
     * AI analysis section is shown below market data if analysis has been run.
     */
    function renderDetailPanel(sym) {
        const panel = document.getElementById('market-detail-panel');
        if (!panel) return;

        const snap = data[sym];

        if (!snap || snap === 'loading' || snap === null) {
            panel.style.display = 'none';
            return;
        }

        const pctClass = changeClass(snap.price_change_pct);

        // ── AI analysis section ──────────────────────────────────────────────
        let aiHtml = '';
        const ai = aiData[sym];

        if (ai === 'loading') {
            aiHtml = `
            <div class="ai-analysis-section" style="margin-top:20px; border-top:2px solid var(--border); padding-top:20px;">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:10px;">
                    <h3 style="margin:0;">AI MARKET ANALYSIS</h3>
                </div>
                <button disabled class="btn-refresh" id="btn-analyze-${sym}" style="width:100%; padding:10px;">
                    Analyzing ${sym}…
                </button>
            </div>`;
        } else if (typeof ai === 'string') {
            // ai holds an error message string
            aiHtml = `
            <div class="ai-analysis-section" style="margin-top:20px; border-top:2px solid var(--border); padding-top:20px;">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:10px;">
                    <h3 style="margin:0;">AI MARKET ANALYSIS</h3>
                </div>
                <button onclick="MW.analyze('${sym}')" class="btn-refresh" id="btn-analyze-${sym}" style="width:100%; padding:10px; font-weight:bold;">
                    Analyze with AI
                </button>
                <div class="box" style="margin-top:12px; border-left:3px solid var(--danger);">
                    <strong style="color:var(--danger);">Analysis failed</strong>
                    <p style="margin-top:5px; font-size:0.85rem;">${ai}</p>
                </div>
            </div>`;
        } else if (ai && ai.analysis) {
            const analysis = ai.analysis;
            const actionColor = analysis.action === 'BUY'
                ? 'var(--success)'
                : analysis.action === 'SELL'
                    ? 'var(--danger)'
                    : 'var(--text-secondary)';

            // Stale analysis check: compare the timestamp from the AI's snapshot
            // against the timestamp currently showing in Market Watch
            let staleWarning = '';
            if (ai.market_snapshot && ai.market_snapshot.timestamp !== snap.timestamp) {
                staleWarning = `
                <div class="box" style="border-left:3px solid var(--warning); margin-bottom:12px; padding:10px;">
                    <span style="color:var(--warning); font-weight:bold;">⚠ Analysis based on earlier market data — re-analyze to update.</span>
                </div>`;
            }

            const assumptionItems = (analysis.assumptions || [])
                .map(a => `<li>${a}</li>`).join('');
            const invalidItems = (analysis.invalidation_conditions || [])
                .map(c => `<li>${c}</li>`).join('');

            // Step 10: Evidence Audit HTML in Market Watch
            let evidenceAuditSectionHtml = '';
            if (ai.evidence_audit) {
                evidenceAuditSectionHtml = `
                <div class="box" style="margin-top:15px; border-left: 3px solid var(--accent);">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                        <strong>AI Decision Evidence Audit</strong>
                        <span class="badge ${ai.evidence_audit.overall_status === 'PASSED' ? 'supported' : (ai.evidence_audit.overall_status === 'FAILED' ? 'unsupported' : 'inconclusive')}">${ai.evidence_audit.overall_status}</span>
                    </div>
                    ${renderEvidenceAuditHtml(ai.evidence_audit)}
                </div>`;
            }

            let riskSectionHtml = '';
            if (analysis.action === 'NO_TRADE') {
                riskSectionHtml = `
                    <div style="background:rgba(255,255,255,0.04); border-radius:4px; padding:12px; margin-top:15px; border-top: 1px solid var(--border);">
                        <div class="metric"><span class="metric-label">Status</span><span class="metric-value text-warning">ADVISORY ONLY</span></div>
                        <div class="metric"><span class="metric-label">Risk Status</span><span class="metric-value">NOT APPLICABLE</span></div>
                        <p style="margin-top:8px; font-size:0.78rem; color:var(--text-secondary);">Reason: AI recommendation does not contain an executable trade proposal.</p>
                    </div>`;
            } else if (ai.risk) {
                if (ai.risk.error && ai.risk.status === 409) {
                     riskSectionHtml = `
                        <div class="box" style="border-left:3px solid var(--warning); margin-top:12px;">
                            <strong style="color:var(--warning);">Stale Analysis</strong>
                            <p style="margin-top:5px; font-size:0.85rem;">${ai.risk.message}</p>
                            <button onclick="MW.analyze('${sym}')" class="btn-refresh" style="margin-top:10px; width:100%; padding:8px;">↻ Re-analyze</button>
                        </div>
                     `;
                } else if (ai.risk.error) {
                    riskSectionHtml = `
                        <div class="box" style="border-left:3px solid var(--danger); margin-top:12px;">
                            <strong style="color:var(--danger);">Risk Evaluation Failed</strong>
                            <p style="margin-top:5px; font-size:0.85rem;">${ai.risk.message}</p>
                        </div>
                    `;
                } else {
                    const riskStatusClass = ai.risk.risk_status === 'APPROVED' ? 'text-success' : (ai.risk.risk_status === 'REJECTED' ? 'text-danger' : 'text-warning');
                    const checksHtml = (ai.risk.risk_reasons || []).map(r => `<li>${r}</li>`).join('');
                    const tradeId = ai.risk.trade_id;

                    // Check if already approved
                    const approval = ai.approval;
                    let approvalHtml = '';
                    if (ai.risk.risk_status === 'APPROVED' && tradeId) {
                        if (approval && approval.success) {
                            // Check if execution result is already available
                            const execution = ai.execution;
                            let executeHtml = '';

                            if (execution && execution.success) {
                                const ex = execution.data;
                                const statusColor = ex.execution_status === 'FILLED'
                                    ? 'var(--success)'
                                    : ex.execution_status === 'REJECTED'
                                        ? 'var(--danger)'
                                        : ex.execution_status === 'PARTIALLY_FILLED'
                                            ? 'var(--warning)'
                                            : 'var(--text-secondary)';
                                executeHtml = `
                                <div style="margin-top:12px; padding:12px; border-radius:6px;
                                            background:rgba(99,102,241,0.1); border:1px solid #6366f1;">
                                    <div style="display:flex; align-items:center; gap:8px; margin-bottom:8px;">
                                        <span style="font-size:1.2rem;">🚀</span>
                                        <strong style="color:#a5b4fc;">ALPACA PAPER ORDER SUBMITTED</strong>
                                    </div>
                                    <div class="metric"><span class="metric-label">Status</span>
                                        <span class="metric-value" style="color:${statusColor}; font-weight:bold;">${ex.execution_status}</span></div>
                                    <div class="metric"><span class="metric-label">Alpaca Order ID</span>
                                        <span class="metric-value uuid" style="font-size:0.72rem;">${ex.alpaca_order_id || 'N/A'}</span></div>
                                    <div class="metric"><span class="metric-label">Client Order ID</span>
                                        <span class="metric-value uuid" style="font-size:0.72rem;">${ex.client_order_id}</span></div>
                                    <div class="metric"><span class="metric-label">Filled Qty</span>
                                        <span class="metric-value">${ex.filled_quantity} / ${ex.quantity}</span></div>
                                    ${ex.average_fill_price ? `<div class="metric"><span class="metric-label">Avg Fill Price</span><span class="metric-value">$${Number(ex.average_fill_price).toFixed(2)}</span></div>` : ''}
                                    <p style="margin-top:8px; font-size:0.78rem; color:var(--text-secondary);">${ex.message}</p>
                                </div>`;
                            } else if (execution && execution.error) {
                                executeHtml = `
                                <div class="box" style="border-left:3px solid var(--danger); margin-top:12px;">
                                    <strong style="color:var(--danger);">Execution Failed</strong>
                                    <p style="margin-top:5px; font-size:0.85rem;">${execution.message}</p>
                                    <button onclick="MW.executeTrade('${tradeId}', '${sym}')" class="btn-refresh"
                                            id="btn-execute-${tradeId}"
                                            style="width:100%; padding:10px; margin-top:8px; font-weight:bold;">
                                        Retry Execution
                                    </button>
                                </div>`;
                            } else {
                                // Show the Execute Paper Trade button with confirmed trade terms
                                const approvedData = approval.data;
                                const actionColor = approvedData.action === 'BUY' ? 'var(--success)' : 'var(--danger)';
                                executeHtml = `
                                <div style="margin-top:14px; padding:14px; border-radius:8px;
                                            background:rgba(99,102,241,0.08); border:1px solid rgba(99,102,241,0.4);">
                                    <div style="margin-bottom:10px;">
                                        <strong style="font-size:0.75rem; text-transform:uppercase;
                                                       letter-spacing:0.08em; color:#a5b4fc;">Step 5 — Execute Paper Trade</strong>
                                        <p style="font-size:0.78rem; color:var(--text-secondary); margin-top:4px;">
                                            This will submit the human-approved proposal to Alpaca Paper Trading.
                                        </p>
                                    </div>
                                    <div style="background:rgba(0,0,0,0.2); border-radius:6px; padding:10px; margin-bottom:12px;">
                                        <div class="metric">
                                            <span class="metric-label">Symbol</span>
                                            <span class="metric-value" style="font-weight:bold;">${approvedData.symbol}</span>
                                        </div>
                                        <div class="metric">
                                            <span class="metric-label">Action</span>
                                            <span class="metric-value" style="color:${actionColor}; font-weight:bold;">${approvedData.action}</span>
                                        </div>
                                        <div class="metric">
                                            <span class="metric-label">Quantity</span>
                                            <span class="metric-value" style="font-weight:bold;">${approvedData.quantity} shares</span>
                                        </div>
                                        <div class="metric">
                                            <span class="metric-label">Order Type</span>
                                            <span class="metric-value">MARKET</span>
                                        </div>
                                        <div class="metric">
                                            <span class="metric-label">Reference Price</span>
                                            <span class="metric-value">$${Number(approvedData.reference_price).toFixed(2)}</span>
                                        </div>
                                    </div>
                                    <button onclick="MW.executeTrade('${tradeId}', '${sym}')" class="btn-refresh"
                                            id="btn-execute-${tradeId}"
                                            style="width:100%; padding:12px; font-weight:bold;
                                                   background:linear-gradient(135deg,rgba(99,102,241,0.3),rgba(99,102,241,0.1));
                                                   border:1px solid #6366f1; color:#a5b4fc; border-radius:6px;">
                                        🚀 Execute Paper Trade
                                    </button>
                                </div>`;
                            }

                            approvalHtml = `
                            <div style="margin-top:12px; padding:12px; border-radius:6px; background:rgba(34,197,94,0.1); border:1px solid var(--success);">
                                <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">
                                    <span style="font-size:1.2rem;">✅</span>
                                    <strong style="color:var(--success);">HUMAN APPROVAL GRANTED</strong>
                                </div>
                                <div class="metric"><span class="metric-label">Trade ID</span><span class="metric-value uuid" style="font-size:0.72rem;">${tradeId}</span></div>
                                <div class="metric"><span class="metric-label">Approved at</span><span class="metric-value" style="font-size:0.8rem;">${approval.data.human_approved_at ? new Date(approval.data.human_approved_at).toLocaleString() : '—'}</span></div>
                            </div>
                            ${executeHtml}`;
                        } else if (approval && approval.error) {
                            const is409 = approval.status === 409;
                            approvalHtml = `
                            <div class="box" style="border-left:3px solid ${is409 ? 'var(--warning)' : 'var(--danger)'}; margin-top:12px;">
                                <strong style="color:${is409 ? 'var(--warning)' : 'var(--danger)'};">Approval Failed</strong>
                                <p style="margin-top:5px; font-size:0.85rem;">${approval.message}</p>
                                ${is409 ? `<button onclick="MW.analyze('${sym}')" class="btn-refresh" style="margin-top:8px; width:100%; padding:8px;">↻ Re-analyze</button>` : ''}
                            </div>`;
                        } else {
                            approvalHtml = `
                            <button onclick="MW.approveTrade('${tradeId}', '${sym}')" class="btn-refresh"
                                    id="btn-approve-${tradeId}"
                                    style="width:100%; padding:12px; margin-top:12px; font-weight:bold;
                                           background:linear-gradient(135deg,rgba(34,197,94,0.2),rgba(34,197,94,0.05));
                                           border:1px solid var(--success); color:var(--success); border-radius:6px;">
                                ✓ Approve Trade
                            </button>
                            <p style="font-size:0.72rem; color:var(--text-secondary); text-align:center; margin-top:6px;">
                                Explicit human approval. No Alpaca order will be created in this step.
                            </p>`;
                        }
                    }

                    riskSectionHtml = `
                    <div style="background:rgba(255,255,255,0.04); border-radius:4px; padding:12px; margin-top:15px; border-top: 1px solid var(--border);">
                        <div class="metric"><span class="metric-label">Status</span><span class="metric-value text-warning">${ai.risk.status}</span></div>
                        <div class="metric"><span class="metric-label">Risk Status</span><span class="metric-value ${riskStatusClass}" style="font-weight:bold;">${ai.risk.risk_status}</span></div>
                        <ul style="margin-top:8px; padding-left:1.2rem; font-size:0.85rem; color:var(--text-secondary);">
                            ${checksHtml}
                        </ul>
                        <div style="margin-top:10px; padding-top:10px; border-top:1px dashed var(--border);">
                            <p style="font-size:0.75rem; color:var(--warning); font-weight:bold; margin-bottom:4px;">${ai.risk.message_human}</p>
                            <p style="font-size:0.75rem; color:var(--text-secondary);">${ai.risk.message_risk}</p>
                        </div>
                        ${approvalHtml}
                    </div>`;
                }
            } else {
                 riskSectionHtml = `
                    <div style="background:rgba(255,255,255,0.04); border-radius:4px; padding:12px; margin-top:15px; border-top: 1px solid var(--border);">
                        <div class="metric"><span class="metric-label">Status</span><span class="metric-value text-warning">ADVISORY ONLY</span></div>
                        <div class="metric"><span class="metric-label">Risk Status</span><span class="metric-value">NOT EVALUATED</span></div>
                        <p style="margin-top:8px; font-size:0.78rem; color:var(--text-secondary);">Risk evaluation has not been performed. AI recommendations cannot bypass TradePilot's deterministic Risk Engine.</p>
                        <button onclick="MW.evaluateRisk('${sym}')" class="btn-refresh" id="btn-evaluate-risk-${sym}" style="width:100%; padding:10px; margin-top:10px; font-weight:bold; background-color:rgba(255,255,255,0.1);">
                            Evaluate Risk
                        </button>
                    </div>`;
            }

            aiHtml = `
            <div class="ai-analysis-section" style="margin-top:20px; border-top:2px solid var(--border); padding-top:20px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;">
                    <h3 style="margin:0;">AI MARKET ANALYSIS</h3>
                    <button onclick="MW.analyze('${sym}')" class="btn-refresh" id="btn-analyze-${sym}">↻ Re-analyze</button>
                </div>
                ${staleWarning}
                <div class="box" style="border-left:3px solid ${actionColor}; margin-bottom:0;">
                    <div class="metric" style="margin-bottom:8px;">
                        <span class="metric-label">Recommendation</span>
                        <span class="metric-value" style="color:${actionColor}; font-weight:bold; font-size:1.3rem; letter-spacing:0.05em;">${analysis.action.replace('_', ' ')}</span>
                    </div>
                    <div class="metric" style="margin-bottom:15px;">
                        <span class="metric-label">AI Confidence</span>
                        <span class="metric-value">${(analysis.confidence * 100).toFixed(0)}%</span>
                    </div>

                    <div style="margin-bottom:12px;">
                        <strong style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-secondary);">Thesis</strong>
                        <p style="margin-top:5px;">${analysis.thesis}</p>
                    </div>

                    <div style="margin-bottom:12px;">
                        <strong style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-secondary);">Assumptions</strong>
                        <ul style="margin-top:5px; padding-left:1.2rem;">${assumptionItems}</ul>
                    </div>

                    <div style="margin-bottom:15px;">
                        <strong style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-secondary);">Invalidation Conditions</strong>
                        <ul style="margin-top:5px; padding-left:1.2rem;">${invalidItems}</ul>
                    </div>

                    <div style="border-top:1px solid var(--border); padding-top:15px; margin-bottom:12px;">
                        <strong style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-secondary);">Evidence Used For This Analysis</strong>
                        <div class="market-detail-grid" style="margin-top:10px;">
                            <div class="metric"><span class="metric-label">Latest Price</span><span class="metric-value">${fmtPrice(ai.market_snapshot.current_price)}</span></div>
                            <div class="metric"><span class="metric-label">Previous Close</span><span class="metric-value">${fmtPrice(ai.market_snapshot.previous_close)}</span></div>
                            <div class="metric"><span class="metric-label">Daily Change</span><span class="metric-value ticker-change ${changeClass(ai.market_snapshot.price_change_pct)}">${fmtPct(ai.market_snapshot.price_change_pct)}</span></div>
                            <div class="metric"><span class="metric-label">SMA20</span><span class="metric-value">${fmtPrice(ai.market_snapshot.sma_20)}</span></div>
                            <div class="metric"><span class="metric-label">SMA50</span><span class="metric-value">${fmtPrice(ai.market_snapshot.sma_50)}</span></div>
                            <div class="metric"><span class="metric-label">Volatility</span><span class="metric-value">${Number(ai.market_snapshot.volatility).toFixed(4)}</span></div>
                            <div class="metric"><span class="metric-label">Volume</span><span class="metric-value">${fmtVol(ai.market_snapshot.volume)}</span></div>
                            <div class="metric" style="grid-column:1/-1; margin-top:6px;">
                                <span class="metric-label">Data timestamp</span>
                                <span class="metric-value" style="font-size:0.8rem; color:var(--text-secondary);">${fmtTimestamp(ai.market_snapshot.timestamp)}</span>
                            </div>
                        </div>
                    </div>

                    ${evidenceAuditSectionHtml}
                    ${riskSectionHtml}
                </div>
            </div>`;
        } else {
            // No analysis yet — show the initial button
            aiHtml = `
            <div class="ai-analysis-section" style="margin-top:20px; border-top:2px solid var(--border); padding-top:20px;">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:10px;">
                    <h3 style="margin:0;">AI MARKET ANALYSIS</h3>
                </div>
                <p style="font-size:0.85rem; color:var(--text-secondary); margin-bottom:12px;">
                    Based on latest available market data. Analysis is on-demand only — Gemini is not called automatically.
                </p>
                <button onclick="MW.analyze('${sym}')" class="btn-refresh" id="btn-analyze-${sym}" style="width:100%; padding:10px; font-weight:bold;">
                    Analyze with AI
                </button>
            </div>`;
        }

        panel.style.display = '';
        panel.innerHTML = `
          <div class="market-detail-header">
            <div class="market-detail-symbol">${snap.symbol}</div>
            <div class="market-detail-note">
              Latest market data (daily bar)<br>
              Not real-time streaming data
            </div>
          </div>
          <div class="market-detail-grid">
            <div class="metric">
              <span class="metric-label">Latest Price</span>
              <span class="metric-value">${fmtPrice(snap.current_price)}</span>
            </div>
            <div class="metric">
              <span class="metric-label">Previous Close</span>
              <span class="metric-value">${fmtPrice(snap.previous_close)}</span>
            </div>
            <div class="metric">
              <span class="metric-label">Daily Change</span>
              <span class="metric-value ticker-change ${pctClass}">${fmtPct(snap.price_change_pct)}</span>
            </div>
            <div class="metric">
              <span class="metric-label">SMA20</span>
              <span class="metric-value">${fmtPrice(snap.sma_20)}</span>
            </div>
            <div class="metric">
              <span class="metric-label">SMA50</span>
              <span class="metric-value">${fmtPrice(snap.sma_50)}</span>
            </div>
            <div class="metric">
              <span class="metric-label">Volatility</span>
              <span class="metric-value">${Number(snap.volatility).toFixed(4)}</span>
            </div>
            <div class="metric">
              <span class="metric-label">Volume</span>
              <span class="metric-value">${fmtVol(snap.volume)}</span>
            </div>
            <div class="metric" style="grid-column: 1 / -1; border-top: 1px solid var(--border); padding-top: 10px; margin-top: 6px;">
              <span class="metric-label">Data timestamp</span>
              <span class="metric-value" style="font-size:0.8rem; color:var(--text-secondary);">${fmtTimestamp(snap.timestamp)}</span>
            </div>
          </div>
          ${aiHtml}`;
    }

    // ── Public API ────────────────────────────────────────────────────────────

    /**
     * Select or deselect a symbol. Clicking the same symbol twice deselects it.
     * Called from inline onclick handlers in the ticker row HTML.
     */
    function select(sym) {
        const snap = data[sym];
        if (!snap || snap === 'loading' || snap === null) return;

        if (selected === sym) {
            // Deselect
            selected = null;
            const panel = document.getElementById('market-detail-panel');
            if (panel) panel.style.display = 'none';
        } else {
            selected = sym;
            renderDetailPanel(sym);
        }
        // Update selected state on ticker cards
        renderTickerRow();
    }

    // ── Initialisation ────────────────────────────────────────────────────────

    function init() {
        // Initial data load
        refresh();

        // Auto-refresh every 30 seconds
        timer = setInterval(refresh, REFRESH_MS);

        // Manual refresh button
        const btn = document.getElementById('market-refresh-btn');
        if (btn) {
            btn.addEventListener('click', () => refresh());
        }
    }

    // Expose only what inline HTML onclick handlers and tests need
    // MW.analyze is exposed so the inline onclick="MW.analyze('SYM')" button works
    return { init, select, refresh, analyze: analyzeSymbol, evaluateRisk, approveTrade, executeTrade, _data: () => data, _aiData: () => aiData };
})();

// Initialise Market Watch after DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    MW.init();
    
    // Initialize backtesting UI
    const runBtn = document.getElementById('bt-run-btn');
    if (runBtn) {
        runBtn.addEventListener('click', runBacktest);
    }
});

// ============================================================================
// BACKTESTING
// ============================================================================

async function runBacktest() {
    const symbol = document.getElementById('bt-symbol').value;
    const startDate = document.getElementById('bt-start').value;
    const endDate = document.getElementById('bt-end').value;
    const capital = document.getElementById('bt-capital').value;
    const statusEl = document.getElementById('bt-status');
    const resultsEl = document.getElementById('bt-results');
    
    if (!symbol || !startDate || !endDate || !capital) {
        statusEl.textContent = 'Please fill in all fields.';
        return;
    }
    
    statusEl.textContent = 'Running historical backtest...';
    resultsEl.style.display = 'none';
    
    try {
        const res = await fetch('/api/backtest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol: symbol,
                start_date: startDate,
                end_date: endDate,
                initial_capital: parseFloat(capital)
            })
        });
        
        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.detail || `HTTP ${res.status}`);
        }
        
        const data = await res.json();
        
        statusEl.textContent = '';
        renderBacktestResults(data);
        resultsEl.style.display = 'block';
    } catch (e) {
        console.error(e);
        statusEl.textContent = `Error: ${e.message}`;
    }
}

function renderBacktestResults(data) {
    // ── Helpers ──────────────────────────────────────────────────────────────
    function fmtUSD(v) { return v == null ? '—' : `$${Number(v).toFixed(2)}`; }
    function fmtPct(v) { return v == null ? '—' : `${(Number(v) * 100).toFixed(2)}%`; }
    function colorClass(v) { return v == null ? '' : (v >= 0 ? 'text-success' : 'text-danger'); }

    // Profit factor: None from backend -> show Inf (No Losses) if there were wins, else dash
    function fmtProfitFactor(pf, grossProfits) {
        if (pf != null) return Number(pf).toFixed(2);
        if (grossProfits > 0) return 'Inf (No Losses)';
        return '&mdash;';
    }
    const grossProfits = (data.avg_winning_trade != null && data.winning_trades > 0)
        ? data.avg_winning_trade * data.winning_trades : 0;

    // ── Summary metrics ───────────────────────────────────────────────────────
    const realizedPnl  = data.realized_pnl   != null ? data.realized_pnl  : data.total_pnl;
    const unrealizedPnl = data.unrealized_pnl != null ? data.unrealized_pnl : 0;

    const sumEl = document.getElementById('bt-summary');
    sumEl.innerHTML = `
        <div class="bt-metrics-group">
            <h4 class="bt-group-label">Capital &amp; Returns</h4>
            <div class="metric" title="Starting cash available to the strategy.">
                <span class="metric-label">Initial Capital:</span>
                <span class="metric-value">${fmtUSD(data.initial_capital)}</span>
            </div>
            <div class="metric" title="Cash + mark-to-market value of open positions at end of backtest period.">
                <span class="metric-label">Final Equity:</span>
                <span class="metric-value">${fmtUSD(data.final_equity)}</span>
            </div>
            <div class="metric" title="(Final Equity - Initial Capital) / Initial Capital.">
                <span class="metric-label">Total Return:</span>
                <span class="metric-value ${colorClass(data.total_return_pct)}">${fmtPct(data.total_return_pct)}</span>
            </div>
            <div class="metric" title="Percentage peak-to-trough decline in portfolio equity during the test period.">
                <span class="metric-label">Max Drawdown:</span>
                <span class="metric-value text-danger">${fmtPct(data.max_drawdown)}</span>
            </div>
        </div>

        <div class="bt-metrics-group">
            <h4 class="bt-group-label">P&amp;L Breakdown</h4>
            <div class="metric" title="Sum of P&amp;L from closed (exited) trades only. Does not include open positions.">
                <span class="metric-label">Realized P&amp;L:</span>
                <span class="metric-value ${colorClass(realizedPnl)}">${fmtUSD(realizedPnl)}</span>
            </div>
            <div class="metric" title="Mark-to-market gain/loss on positions still open at end of the period (last close - entry cost).">
                <span class="metric-label">Unrealized P&amp;L:</span>
                <span class="metric-value ${colorClass(unrealizedPnl)}">${fmtUSD(unrealizedPnl)}</span>
            </div>
            <div class="metric" title="Accounting check: Final Equity = Initial Capital + Realized P&amp;L + Unrealized P&amp;L.">
                <span class="metric-label">Open Positions:</span>
                <span class="metric-value">${data.open_positions_at_end != null ? data.open_positions_at_end : 0}</span>
            </div>
        </div>

        <div class="bt-metrics-group">
            <h4 class="bt-group-label">Trade Statistics (Closed Trades Only)</h4>
            <div class="metric" title="Number of round-trip trades that were fully closed during the period.">
                <span class="metric-label">Closed Trades:</span>
                <span class="metric-value">${data.total_trades}</span>
            </div>
            <div class="metric" title="Closed trades with P&amp;L &gt; 0.">
                <span class="metric-label">Winning Trades:</span>
                <span class="metric-value text-success">${data.winning_trades}</span>
            </div>
            <div class="metric" title="Closed trades with P&amp;L &lt; 0.">
                <span class="metric-label">Losing Trades:</span>
                <span class="metric-value text-danger">${data.losing_trades}</span>
            </div>
            <div class="metric" title="Winning Trades / Closed Trades. Excludes open positions.">
                <span class="metric-label">Win Rate:</span>
                <span class="metric-value">${fmtPct(data.win_rate)}</span>
            </div>
            <div class="metric" title="Average P&amp;L of winning closed trades.">
                <span class="metric-label">Avg Win:</span>
                <span class="metric-value text-success">${fmtUSD(data.avg_winning_trade)}</span>
            </div>
            <div class="metric" title="Average P&amp;L of losing closed trades (negative value).">
                <span class="metric-label">Avg Loss:</span>
                <span class="metric-value text-danger">${fmtUSD(data.avg_losing_trade)}</span>
            </div>
            <div class="metric" title="Gross Profit / Gross Loss. Inf (No Losses) means no losing trades occurred.">
                <span class="metric-label">Profit Factor:</span>
                <span class="metric-value">${fmtProfitFactor(data.profit_factor, grossProfits)}</span>
            </div>
        </div>
    `;

    // ── Benchmark comparison card ─────────────────────────────────────────────
    const benchEl = document.getElementById('bt-benchmark');
    if (benchEl) {
        if (data.benchmark) {
            const bm = data.benchmark;
            const stratRetPct = data.total_return_pct * 100;
            const bmRetPct    = bm.total_return_pct * 100;
            const alpha       = stratRetPct - bmRetPct;
            const alphaClass  = alpha >= 0 ? 'text-success' : 'text-danger';
            const alphaSign   = alpha >= 0 ? '+' : '';
            benchEl.style.display = 'block';
            benchEl.innerHTML = `
                <h3 style="margin-bottom:10px;">Strategy vs. Buy-and-Hold Benchmark</h3>
                <p style="font-size:0.85em; color:var(--text-muted); margin-bottom:14px;">
                    <strong>Buy-and-hold benchmark:</strong> Fractional shares of <em>${bm.symbol}</em> purchased at the
                    open of the first available decision bar ($${Number(bm.start_price).toFixed(2)}) and held through the
                    close of the last bar ($${Number(bm.end_price).toFixed(2)}).
                    100% of initial capital deployed. <strong>This is a comparison only, not a tradable recommendation.</strong>
                </p>
                <div style="display:flex; flex-wrap:wrap; gap:30px; align-items:flex-start;">
                    <div class="bt-metrics-group" style="min-width:180px;">
                        <h4 class="bt-group-label">Strategy</h4>
                        <div class="metric">
                            <span class="metric-label">Final Equity:</span>
                            <span class="metric-value">${fmtUSD(data.final_equity)}</span>
                        </div>
                        <div class="metric">
                            <span class="metric-label">Total Return:</span>
                            <span class="metric-value ${colorClass(data.total_return_pct)}">${stratRetPct.toFixed(2)}%</span>
                        </div>
                    </div>
                    <div class="bt-metrics-group" style="min-width:180px;">
                        <h4 class="bt-group-label">Buy &amp; Hold</h4>
                        <div class="metric">
                            <span class="metric-label">Final Equity:</span>
                            <span class="metric-value">${fmtUSD(bm.final_equity)}</span>
                        </div>
                        <div class="metric">
                            <span class="metric-label">Total Return:</span>
                            <span class="metric-value ${colorClass(bm.total_return_pct)}">${bmRetPct.toFixed(2)}%</span>
                        </div>
                    </div>
                    <div class="bt-metrics-group" style="min-width:180px;">
                        <h4 class="bt-group-label">Alpha (Strategy - Benchmark)</h4>
                        <div class="metric">
                            <span class="metric-label">Return Difference:</span>
                            <span class="metric-value ${alphaClass}">${alphaSign}${alpha.toFixed(2)}%</span>
                        </div>
                    </div>
                </div>
            `;
        } else {
            benchEl.style.display = 'block';
            benchEl.innerHTML = `<p style="font-size:0.85em; color:var(--text-muted);">Buy-and-hold benchmark unavailable (insufficient historical data for this period).</p>`;
        }
    }

    const tradesBody = document.getElementById('bt-trades-body');
    let tradesHtml = '';
    
    // Sort trades by entry date descending
    const sortedTrades = [...(data.trades || [])].reverse();
    
    sortedTrades.forEach(t => {
        const isOpen = t.exit_date === null;
        const statusStr = isOpen ? '<span class="badge warning">OPEN</span>' : '<span class="badge neutral">CLOSED</span>';
        
        const exitDateStr = isOpen ? '—' : t.exit_date;
        const exitPriceStr = isOpen ? '—' : `$${t.exit_price.toFixed(2)}`;
        
        let pnlStr = '—';
        let pnlPctStr = '—';
        let pnlClass = '';
        if (!isOpen && t.pnl !== null) {
            pnlStr = `$${t.pnl.toFixed(2)}`;
            pnlPctStr = `${(t.pnl_pct * 100).toFixed(2)}%`;
            pnlClass = t.pnl >= 0 ? 'text-success' : 'text-danger';
        }
        
        tradesHtml += `
            <tr>
                <td>${t.entry_date}</td>
                <td class="${t.action === 'BUY' ? 'text-success' : 'text-danger'}">${t.action}</td>
                <td>${t.quantity}</td>
                <td>$${t.entry_price.toFixed(2)}</td>
                <td>${exitDateStr}</td>
                <td>${exitPriceStr}</td>
                <td>$${t.stop_loss.toFixed(2)}</td>
                <td class="${pnlClass}">${pnlStr}</td>
                <td class="${pnlClass}">${pnlPctStr}</td>
                <td>${t.exit_reason || '—'}</td>
                <td>${statusStr}</td>
            </tr>
        `;
    });
    
    if (sortedTrades.length === 0) {
        tradesHtml = `<tr><td colspan="11" style="text-align:center;">No trades taken during this period.</td></tr>`;
    }
    tradesBody.innerHTML = tradesHtml;
    
    // Render Equity Curve (simple CSS bars)
    const eqChart = document.getElementById('bt-equity-chart');
    eqChart.innerHTML = '';
    
    if (data.equity_curve && data.equity_curve.length > 0) {
        let minEq = Math.min(...data.equity_curve.map(p => p.total_equity));
        let maxEq = Math.max(...data.equity_curve.map(p => p.total_equity));
        
        const range = maxEq - minEq || 1;
        minEq = Math.max(0, minEq - range * 0.1); 
        maxEq = maxEq + range * 0.1;
        
        data.equity_curve.forEach(p => {
            const pctHeight = Math.max(1, ((p.total_equity - minEq) / (maxEq - minEq)) * 100);
            const bar = document.createElement('div');
            bar.style.width = '3px';
            bar.style.flexShrink = '0';
            bar.style.height = `${pctHeight}%`;
            bar.style.backgroundColor = p.total_equity >= data.initial_capital ? 'var(--success)' : 'var(--danger)';
            bar.title = `${p.date}: $${p.total_equity.toFixed(2)}`;
            eqChart.appendChild(bar);
        });
    }
}
