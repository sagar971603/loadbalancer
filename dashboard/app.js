const machines = document.querySelector('#machines');
const summary = document.querySelector('#summary');
const checked = document.querySelector('#checked');
const overall = document.querySelector('#overall');
const overallText = document.querySelector('#overall-text');
const refreshButton = document.querySelector('#refresh');
const confirmDialog = document.querySelector('#confirm');
const confirmTitle = document.querySelector('#confirm-title');
const confirmText = document.querySelector('#confirm-text');
const confirmAction = document.querySelector('#confirm-action');
const toast = document.querySelector('#toast');
let pending = null;

const escapeHtml = value => String(value).replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));

function notify(message) {
  toast.textContent = message;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 3600);
}

function detail(row) {
  const d = row.details || {};
  if (row.service === 'newtool') {
    return `<strong>${row.live_connections || 0}</strong> live &middot; ${d.active_sessions || 0} login &middot; ${d.forgot_password_sessions || 0} reset &middot; ${d.websockets || 0} WS <div class="detail">worker sample</div>`;
  }
  return `<strong>${row.live_connections || 0}</strong> live &middot; ${d.active_sessions || 0} registration &middot; ${d.total_errors || 0} errors`;
}

function egressCell(status) {
  if (!status.configured) return '<span class="pill disabled">Not used</span>';
  const active = status.active === null ? '?' : status.active;
  const live = status.healthy && status.available;
  const label = status.cooldown_seconds ? `Cooling ${status.cooldown_seconds}s` : status.half_open ? 'Recovery probe' : live ? 'Live' : 'Attention';
  const failures = status.consecutive_failures ? ` &middot; ${status.consecutive_failures} network failures` : '';
  return `<span class="pill ${live ? 'healthy' : 'unhealthy'}">${label}</span><div class="slot-count"><strong>${active}</strong> / ${status.limit} active${failures}</div>`;
}

function render(data) {
  const rows = data.machines.flatMap(machine => machine.rows);
  const healthy = rows.filter(row => row.healthy).length;
  const disabled = rows.filter(row => !row.enabled).length;
  const connections = rows.reduce((sum, row) => sum + Number(row.live_connections || 0), 0);
  const sessions = rows.reduce((sum, row) => sum + Number(row.details?.active_sessions || 0) + Number(row.service === 'newtool' ? row.details?.forgot_password_sessions || 0 : 0), 0);
  const websockets = rows.reduce((sum, row) => sum + Number(row.details?.websockets || 0), 0);

  summary.innerHTML = [
    ['Healthy routes', `${healthy}/${rows.length}`],
    ['Live connections', connections],
    ['Sampled sessions', sessions],
    ['WebSockets', websockets],
    ['Disabled routes', disabled],
  ].map(([label, value]) => `<article class="metric"><span>${label}</span><strong>${value}</strong></article>`).join('');

  const allGood = healthy === rows.length && disabled === 0;
  overall.className = `status-dot ${allGood ? 'good' : 'bad'}`;
  overallText.textContent = allGood ? 'All configured routes healthy' : `${rows.length - healthy} unhealthy - ${disabled} disabled`;
  checked.textContent = `Updated ${new Date(data.checked_at).toLocaleTimeString()} - auto-refresh 10 seconds`;

  machines.innerHTML = data.machines.map(machine => `
    <article class="machine">
      <div class="machine-head">
        <div><h2>${escapeHtml(machine.label)}</h2><div class="ip-list">Outgoing: ${machine.outgoing.map(item => escapeHtml(item.ip)).join(' &middot; ')}</div></div>
        <span class="pill ${machine.rows.length === 2 && machine.rows.every(row => row.healthy) ? 'healthy' : 'unhealthy'}">${machine.rows.length === 2 && machine.rows.every(row => row.healthy) ? 'Healthy' : 'Attention'}</span>
      </div>
      <div class="rows"><table>
        <thead><tr><th>Application</th><th>Incoming IP</th><th>Health</th><th>Sessions</th><th>Route</th><th>Control</th></tr></thead>
        <tbody>${machine.rows.map(row => `
          <tr>
            <td class="service">${row.service === 'newtool' ? 'Newtool2' : 'Registration'}</td>
            <td class="mono">${escapeHtml(row.ip)}:${row.port}</td>
            <td><span class="pill ${row.healthy ? 'healthy' : 'unhealthy'}">${row.healthy ? `${row.latency_ms} ms` : 'Unhealthy'}</span>${row.error ? `<div class="detail">${escapeHtml(row.error)}</div>` : ''}</td>
            <td class="detail">${detail(row)}</td>
            <td><span class="pill ${row.enabled ? 'enabled' : 'disabled'}">${row.enabled ? 'Enabled' : 'Disabled'}</span></td>
            <td><button class="${row.enabled ? 'danger' : 'secondary'} toggle" data-service="${row.service}" data-ip="${row.ip}" data-action="${row.enabled ? 'disable' : 'enable'}">${row.enabled ? 'Disable' : 'Enable'}</button></td>
          </tr>`).join('')}</tbody>
      </table></div>
      <div class="egress">
        <h3>Outgoing IP live capacity</h3>
        <div class="rows"><table class="egress-table">
          <thead><tr><th>Outgoing IP</th><th>Newtool2</th><th>Registration</th><th>Note</th></tr></thead>
          <tbody>${machine.outgoing.map(item => `<tr>
            <td class="mono">${escapeHtml(item.ip)}</td>
            <td>${egressCell(item.services.newtool)}</td>
            <td>${egressCell(item.services.registration)}</td>
            <td class="detail">${escapeHtml(item.note || 'Available for portal traffic')}</td>
          </tr>`).join('')}</tbody>
        </table></div>
      </div>
    </article>`).join('');

  document.querySelectorAll('.toggle').forEach(button => button.addEventListener('click', () => {
    pending = {...button.dataset};
    confirmTitle.textContent = `${pending.action === 'disable' ? 'Disable' : 'Enable'} ${pending.ip}?`;
    confirmText.textContent = `${pending.service === 'newtool' ? 'Newtool2' : 'Registration'} traffic through this incoming IP will be ${pending.action}d. Existing connections are not force-closed.`;
    confirmAction.className = pending.action === 'disable' ? 'danger' : '';
    confirmDialog.showModal();
  }));
}

async function refresh() {
  refreshButton.disabled = true;
  try {
    const response = await fetch('api/status', {cache: 'no-store'});
    if (!response.ok) throw new Error(`Status request failed (${response.status})`);
    render(await response.json());
  } catch (error) {
    overall.className = 'status-dot bad';
    overallText.textContent = 'Dashboard cannot read backend status';
    notify(error.message);
  } finally {
    refreshButton.disabled = false;
  }
}

confirmDialog.addEventListener('close', async () => {
  if (confirmDialog.returnValue !== 'confirm' || !pending) return;
  const action = pending;
  pending = null;
  notify(`${action.action === 'disable' ? 'Disabling' : 'Enabling'} route...`);
  try {
    const response = await fetch('api/toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Dashboard-Action': 'toggle'},
      body: JSON.stringify(action),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Control failed');
    render(result.status);
    notify(result.message || 'Route updated safely');
  } catch (error) {
    notify(error.message);
    await refresh();
  }
});

refreshButton.addEventListener('click', refresh);
refresh();
setInterval(refresh, 10000);
