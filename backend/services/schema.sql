CREATE TABLE IF NOT EXISTS jobs (
 job_id TEXT PRIMARY KEY, mode TEXT NOT NULL, prompt TEXT NOT NULL,
 parameters_json TEXT NOT NULL, input_files_json TEXT NOT NULL, backend TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','canceled','interrupted')),
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, output_path TEXT,
 error_message TEXT, comfy_prompt_id TEXT, stage TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS one_running ON jobs(status) WHERE status='running';
CREATE TABLE IF NOT EXISTS worker_state (id INTEGER PRIMARY KEY CHECK(id=1), heartbeat TEXT NOT NULL, backend TEXT NOT NULL);
