const RULES = [
  {
    re: /Airflow trigger failed \((\d+)\)/i,
    map: (m, raw) => ({
      title:  'Could not start the analysis',
      detail: `Airflow rejected the trigger (HTTP ${m[1]}). The pipeline definition or our trigger format is out of sync. Try restarting the run; if it keeps failing, send the run id to the team.`,
      raw,
    }),
  },
  {
    re: /Cannot reach Airflow at .*Name or service not known/i,
    map: (_, raw) => ({
      title:  'Pipeline server is unreachable',
      detail: 'The backend can\'t reach the Airflow service. Confirm the Airflow stack is running and that AIRFLOW_BASE_URL points at a host the backend can resolve.',
      raw,
    }),
  },
  {
    re: /Airflow request failed.*ConnectError/i,
    map: (_, raw) => ({
      title:  'Pipeline server is unreachable',
      detail: 'The backend could not open a TCP connection to Airflow. Check whether Airflow webserver / api-server is up.',
      raw,
    }),
  },
  {
    re: /Airflow auth failed \((\d+)\)/i,
    map: (m, raw) => ({
      title:  'Pipeline credentials rejected',
      detail: `Airflow returned HTTP ${m[1]} on the auth endpoint. The AIRFLOW_USERNAME / AIRFLOW_PASSWORD that the backend uses are wrong, or the auth manager changed.`,
      raw,
    }),
  },
  {
    re: /Unknown run_id/i,
    map: (_, raw) => ({
      title:  'Run not found',
      detail: 'Airflow does not know about this run id. It may have been deleted in the Airflow UI. Close this tab and trigger a new analysis.',
      raw,
    }),
  },
  {
    re: /Aligned NASADEM is empty.*valid pixels/i,
    map: (_, raw) => ({
      title:  'DEMs do not overlap',
      detail: 'The baseline and modern DEMs don\'t cover the same area — usually because a previous run for a different bbox is cached under the same site folder. Delete the matching DEM_Downloads/<site>/ folder and restart the pipeline.',
      raw,
    }),
  },
  {
    re: /Cannot connect to Azure Blob Storage/i,
    map: (_, raw) => ({
      title:  'Azure connection misconfigured',
      detail: 'The Airflow connection used to upload artifacts is missing or invalid. Open Airflow → Admin → Connections and verify the Azure connection string.',
      raw,
    }),
  },
  {
    re: /Expected artifact not produced/i,
    map: (_, raw) => ({
      title:  'A pipeline step produced no output',
      detail: 'The script before the upload finished but did not write the expected file. Open that step\'s log (click the step in the panel) to see the real cause.',
      raw,
    }),
  },
  {
    re: /OpenTopography rejected the AW3D30 request/i,
    map: (_, raw) => ({
      title:  'AW3D30 download blocked',
      detail: 'OpenTopography returned an error for the AW3D30 download. Most often this is a rate limit or an invalid API key. Multi-period analysis will be skipped; the main DoD still runs.',
      raw,
    }),
  },
  {
    re: /Zero SRTM tiles downloaded/i,
    map: (_, raw) => ({
      title:  'BBox is over water',
      detail: 'Every SRTM tile for this area returned 404, which means the bbox is entirely over the ocean. Draw a polygon on land and try again.',
      raw,
    }),
  },
  {
    re: /Cannot reach Airflow/i,
    map: (_, raw) => ({
      title:  'Pipeline server is unreachable',
      detail: 'The backend cannot reach Airflow. Confirm the Airflow stack is up.',
      raw,
    }),
  },
  {
    re: /Failed to fetch|NetworkError|ERR_FAILED|Load failed/i,
    map: (_, raw) => ({
      title:  'Could not reach the backend',
      detail: 'The backend dropped the connection or did not respond in time. This usually means the API container is restarting, the request crashed mid-response, or the backend is overloaded. Try again in a few seconds.',
      raw,
    }),
  },
  {
    re: /AbortError|signal is aborted/i,
    map: (_, raw) => ({
      title:  'Request was cancelled',
      detail: 'The request was aborted before it finished. This happens if you navigate away mid-fetch or close the tab.',
      raw,
    }),
  },
  {
    re: /Log not available yet/i,
    map: (_, raw) => ({
      title:  'Logs aren\'t ready yet',
      detail: 'This task hasn\'t produced any log output yet — usually because it is still queued or just started. Click Refresh in a few seconds.',
      raw,
    }),
  },
  {
    re: /HTTP\s+(4\d{2})/,
    map: (m, raw) => ({
      title:  'Pipeline server rejected the request',
      detail: `Airflow returned HTTP ${m[1]}. This usually means the payload format changed or the run/task id is no longer valid.`,
      raw,
    }),
  },
  {
    re: /HTTP\s+(5\d{2})/,
    map: (m, raw) => ({
      title:  'Pipeline server hit an error',
      detail: `Airflow returned HTTP ${m[1]}. The error is on the pipeline server side — check its logs and retry.`,
      raw,
    }),
  },
];

export function prettifyError(raw) {
  if (!raw) return null;
  const text = typeof raw === 'string' ? raw : String(raw);
  for (const rule of RULES) {
    const m = text.match(rule.re);
    if (m) return rule.map(m, text);
  }
  return { title: 'Something went wrong', detail: text, raw: text };
}
