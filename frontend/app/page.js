'use client';

import { useEffect, useMemo, useState } from 'react';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:18080';

const defaultForm = {
  subject: '',
  sender_email: '',
  sender_password: '',
  smtp_host: 'smtp.gmail.com',
  smtp_port: '587',
  daily_limit: '200',
  schedule_time: '09:00',
  mode: 'run_today_then_schedule',
  use_tls: true,
  personalize: false,
};

const defaultList = {
  page: 1,
  pageSize: 25,
  filterStatus: 'all',
  sortBy: 'index',
  sortOrder: 'asc',
};

export default function HomePage() {
  const [csvFile, setCsvFile] = useState(null);
  const [bodyFile, setBodyFile] = useState(null);
  const [resumeFile, setResumeFile] = useState(null);
  const [uploaded, setUploaded] = useState(null);
  const [preview, setPreview] = useState(null);
  const [form, setForm] = useState(defaultForm);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionType, setActionType] = useState('send');
  const [listState, setListState] = useState(defaultList);

  const canUpload = useMemo(() => csvFile && bodyFile && resumeFile, [csvFile, bodyFile, resumeFile]);
  const canSubmit = useMemo(() => uploaded && form.subject && form.sender_email && form.sender_password, [uploaded, form]);

  async function callApi(path, options = {}) {
    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      cache: 'no-store',
      headers: {
        ...(options.headers || {}),
      },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || data.message || `HTTP ${res.status}`);
    }
    return data;
  }

  async function uploadFiles() {
    if (!canUpload) return;
    setBusy(true);
    setResult('Uploading files...');
    try {
      const fd = new FormData();
      fd.append('csv_file', csvFile);
      fd.append('body_file', bodyFile);
      fd.append('resume_file', resumeFile);
      const data = await callApi('/api/upload', { method: 'POST', body: fd });
      setUploaded(data);
      setResult('Upload successful.');
      setListState((s) => ({ ...s, page: 1 }));
    } catch (err) {
      setResult(`Upload failed: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  function payload() {
    if (!uploaded) return null;
    return {
      csv_path: uploaded.csv_path,
      body_path: uploaded.body_path,
      resume_path: uploaded.resume_path,
      subject: form.subject,
      sender_email: form.sender_email,
      sender_password: form.sender_password,
      smtp_host: form.smtp_host,
      smtp_port: Number(form.smtp_port),
      use_tls: !!form.use_tls,
      personalize: !!form.personalize,
      daily_limit: Number(form.daily_limit),
      schedule_time: form.schedule_time,
      mode: form.mode,
    };
  }

  async function refreshPreview() {
    if (!uploaded?.csv_path) return;
    try {
      const q = new URLSearchParams({
        csv_path: uploaded.csv_path,
        page: String(listState.page),
        page_size: String(listState.pageSize),
        filter_status: listState.filterStatus,
        sort_by: listState.sortBy,
        sort_order: listState.sortOrder,
        t: String(Date.now()),
      });
      const data = await callApi(`/api/csv/preview?${q.toString()}`);
      setPreview(data);
    } catch (err) {
      setResult(`Preview failed: ${err.message}`);
    }
  }

  async function sendOnce() {
    if (!canSubmit) return;
    setBusy(true);
    setResult('Sending batch...');
    try {
      const data = await callApi('/api/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload()),
      });
      setResult(`Send completed. Completed: ${data.after.completed}/${data.after.total}`);
      await refreshPreview();
    } catch (err) {
      setResult(`Send failed: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function startJob() {
    if (!canSubmit) return;
    setBusy(true);
    setResult('Starting daily job...');
    try {
      const data = await callApi('/api/job/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload()),
      });
      setJob(data.job);
      setResult('Scheduled job started.');
      await refreshPreview();
    } catch (err) {
      setResult(`Start failed: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function pollStatus() {
    try {
      const data = await callApi('/api/job/status');
      setJob(data);
    } catch {
      // ignore polling errors
    }
  }

  useEffect(() => {
    pollStatus();
    const timer = setInterval(async () => {
      await pollStatus();
      if (uploaded?.csv_path) {
        await refreshPreview();
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [uploaded?.csv_path, listState.page, listState.pageSize, listState.filterStatus, listState.sortBy, listState.sortOrder]);

  useEffect(() => {
    if (uploaded?.csv_path) {
      refreshPreview();
    }
  }, [uploaded?.csv_path, listState.page, listState.pageSize, listState.filterStatus, listState.sortBy, listState.sortOrder]);

  return (
    <main className="page">
      <section className="card">
        <h1>Email Generator</h1>
        <p>Upload files, then choose one action: Send Batch or Schedule Job.</p>

        <div className="grid3">
          <label>
            CSV File
            <input type="file" accept=".csv" onChange={(e) => setCsvFile(e.target.files?.[0] || null)} />
          </label>
          <label>
            Body File
            <input type="file" accept=".txt" onChange={(e) => setBodyFile(e.target.files?.[0] || null)} />
          </label>
          <label>
            Resume File
            <input type="file" accept=".pdf,.doc,.docx" onChange={(e) => setResumeFile(e.target.files?.[0] || null)} />
          </label>
        </div>

        <button disabled={!canUpload || busy} onClick={uploadFiles}>Upload Files</button>

        <div className="grid2">
          <label>Subject
            <input value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
          </label>
          <label>Sender Email
            <input value={form.sender_email} onChange={(e) => setForm({ ...form, sender_email: e.target.value })} />
          </label>
          <label>Sender Password
            <input type="password" value={form.sender_password} onChange={(e) => setForm({ ...form, sender_password: e.target.value })} />
          </label>
          <label>SMTP Host
            <input value={form.smtp_host} onChange={(e) => setForm({ ...form, smtp_host: e.target.value })} />
          </label>
          <label>SMTP Port
            <input value={form.smtp_port} onChange={(e) => setForm({ ...form, smtp_port: e.target.value })} />
          </label>
          <label>Daily Limit (1-250)
            <input
              placeholder="Resumes from pending only; sends this many new pending emails"
              value={form.daily_limit}
              onChange={(e) => setForm({ ...form, daily_limit: e.target.value })}
            />
          </label>
        </div>
        <p className="hint">
          Sends always continue from pending rows only. If you set limit to 8, it sends 8 new pending emails in this run.
        </p>

        <div className="row">
          <label><input type="checkbox" checked={form.use_tls} onChange={(e) => setForm({ ...form, use_tls: e.target.checked })} /> Use TLS</label>
          <label><input type="checkbox" checked={form.personalize} onChange={(e) => setForm({ ...form, personalize: e.target.checked })} /> Personalize</label>
        </div>

        <div className="row">
          <label><input type="radio" name="actionType" checked={actionType === 'send'} onChange={() => setActionType('send')} /> Send Batch</label>
          <label><input type="radio" name="actionType" checked={actionType === 'schedule'} onChange={() => setActionType('schedule')} /> Schedule Job</label>
        </div>

        {actionType === 'schedule' && (
          <div className="grid2">
            <label>Schedule Time (HH:MM)
              <input value={form.schedule_time} onChange={(e) => setForm({ ...form, schedule_time: e.target.value })} />
            </label>
            <label>Start Option
              <select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}>
                <option value="run_today_then_schedule">Start Today Then Follow Schedule</option>
                <option value="schedule_only">Ignore Today, Start On Schedule</option>
              </select>
            </label>
          </div>
        )}

        <div className="row">
          <button disabled={!canSubmit || busy} onClick={sendOnce}>Send Batch</button>
          <button disabled={!canSubmit || busy} onClick={startJob}>Schedule Job</button>
        </div>

        <pre className="status">{result || 'No actions yet.'}</pre>
      </section>

      <section className="card">
        <h2>Job Status</h2>
        <pre className="status">{JSON.stringify(job || {}, null, 2)}</pre>
      </section>

      <section className="card">
        <h2>CSV Preview</h2>
        <p>Total: {preview?.total || 0}, Completed: {preview?.completed || 0}, Pending: {preview?.pending || 0}</p>

        <div className="row controls">
          <label>Filter
            <select value={listState.filterStatus} onChange={(e) => setListState({ ...listState, page: 1, filterStatus: e.target.value })}>
              <option value="all">All</option>
              <option value="completed">Completed</option>
              <option value="pending">Pending</option>
            </select>
          </label>
          <label>Sort By
            <select value={listState.sortBy} onChange={(e) => setListState({ ...listState, page: 1, sortBy: e.target.value })}>
              <option value="index">Index</option>
              <option value="email">Email</option>
              <option value="status">Status</option>
              <option value="completed">Completed Time</option>
            </select>
          </label>
          <label>Order
            <select value={listState.sortOrder} onChange={(e) => setListState({ ...listState, page: 1, sortOrder: e.target.value })}>
              <option value="asc">Asc</option>
              <option value="desc">Desc</option>
            </select>
          </label>
          <label>Page Size
            <select value={listState.pageSize} onChange={(e) => setListState({ ...listState, page: 1, pageSize: Number(e.target.value) })}>
              <option value="10">10</option>
              <option value="25">25</option>
              <option value="50">50</option>
              <option value="100">100</option>
            </select>
          </label>
        </div>

        <div className="tableWrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Email</th>
                <th>Status</th>
                <th>Completed</th>
              </tr>
            </thead>
            <tbody>
              {(preview?.emails || []).map((item) => (
                <tr key={`${item.index}-${item.email}`}>
                  <td>{item.index}</td>
                  <td>{item.email}</td>
                  <td>{item.status}</td>
                  <td>{item.completed || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="row pager">
          <button disabled={busy || (preview?.page || 1) <= 1} onClick={() => setListState({ ...listState, page: Math.max(1, listState.page - 1) })}>Prev</button>
          <span>Page {preview?.page || 1} / {preview?.total_pages || 1}</span>
          <button
            disabled={busy || (preview?.page || 1) >= (preview?.total_pages || 1)}
            onClick={() => setListState({ ...listState, page: (listState.page + 1) })}
          >
            Next
          </button>
        </div>
      </section>
    </main>
  );
}
