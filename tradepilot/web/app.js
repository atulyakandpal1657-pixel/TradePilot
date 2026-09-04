const TRADE_ID = "b6e16912-ee7e-47c6-a51b-a663064adaab"; // The PLTR trade

async function loadDashboard() {
    try {
        const response = await fetch(`/api/trace/${TRADE_ID}`);
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

    // 6. DECISION TRACE TIMELINE
    const steps = [
        "Market Evidence", "AI Strategy", "Confidence", "Thesis", 
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

document.addEventListener('DOMContentLoaded', loadDashboard);
