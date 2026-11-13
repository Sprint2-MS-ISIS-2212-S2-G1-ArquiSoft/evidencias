import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Counter } from 'k6/metrics';
 
const BASE_URL = __ENV.BASE_URL || 'http://localhost:3000';
const SOCIO_ID = __ENV.SOCIO_ID || '123e4567-e89b-12d3-a456-426614174001';
const PRODUCTO_ID = __ENV.PRODUCTO_ID || '123e4567-e89b-12d3-a456-426614174000';
const TERCEROS_ADMIN_URL = __ENV.TERCEROS_ADMIN_URL || '';
const MODO_FALLA = __ENV.MODO_FALLA || 'caido';
const LATENCIA_LENTA_MS = Number(__ENV.LATENCIA_LENTA_MS || 2000);
const FASE_S = Number(__ENV.FASE_S || 300);
const TASA_POR_MIN = Number(__ENV.TASA_POR_MIN || 500);
const DOCUMENTOS = Number(__ENV.DOCUMENTOS || 500);
const CORRIDA = __ENV.CORRIDA || `corrida-${MODO_FALLA}`;
 
const origenScore = new Counter('origen_score');
 
const escenarios = {
  carga: {
    executor: 'constant-arrival-rate',
    rate: TASA_POR_MIN,
    timeUnit: '1m',
    duration: `${FASE_S * 3}s`,
    preAllocatedVUs: 20,
    maxVUs: 200,
    exec: 'cotizar',
  },
};
if (TERCEROS_ADMIN_URL) {
  escenarios.iniciar_falla = {
    executor: 'per-vu-iterations', vus: 1, iterations: 1,
    startTime: `${FASE_S}s`, exec: 'iniciarFalla',
  };
  escenarios.restablecer = {
    executor: 'per-vu-iterations', vus: 1, iterations: 1,
    startTime: `${FASE_S * 2}s`, exec: 'restablecer',
  };
}
 
export const options = {
  scenarios: escenarios,
  // Criterios de la hipotesis, evaluados SOLO en la fase de falla.
  thresholds: {
    'checks{fase:falla}': ['rate>=0.999'],
    'http_req_duration{fase:falla,tipo:cotizacion}': ['p(95)<=250', 'p(99)<=500'],
    // Referencia: las otras fases tambien se reportan.
    'checks{fase:normal}': ['rate>=0'],
    'checks{fase:recuperacion}': ['rate>=0'],
    'http_req_duration{fase:normal,tipo:cotizacion}': ['p(95)>=0'],
    'http_req_duration{fase:recuperacion,tipo:cotizacion}': ['p(95)>=0'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};
 
function faseActual() {
  const transcurrido = (Date.now() - exec.scenario.startTime) / 1000;
  if (transcurrido < FASE_S) return 'normal';
  if (transcurrido < FASE_S * 2) return 'falla';
  return 'recuperacion';
}
 
export function cotizar() {
  const fase = faseActual();
  // Pool fijo de clientes: en la fase normal casi todos quedan con score en cache.
  const n = Math.floor(Math.random() * DOCUMENTOS);
  const documento = `10${String(n).padStart(8, '0')}`;
  const tags = { fase, tipo: 'cotizacion' };
 
  const res = http.post(
    `${BASE_URL}/cotizar`,
    JSON.stringify({
      nombre: `Cliente ${n}`,
      documento,
      idProducto: PRODUCTO_ID,
      aceptaOpenData: true,
    }),
    {
      headers: { 'Content-Type': 'application/json', 'X-Socio-Id': SOCIO_ID },
      tags,
      timeout: '10s',
    },
  );
 
  let cuerpo = null;
  try { cuerpo = res.json(); } catch (e) { cuerpo = null; }
  const origen = (cuerpo && cuerpo.origenScore) || 'ERROR';
  origenScore.add(1, { fase, origen });
 
  check(
    res,
    { 'cotizacion creada (201 con id)': (r) => r.status === 201 && !!(cuerpo && cuerpo.id) },
    { fase },
  );
}
 
function fijarModo(modo) {
  const res = http.post(
    `${TERCEROS_ADMIN_URL}/simulacion/open-data`,
    JSON.stringify({ modo, latenciaMs: LATENCIA_LENTA_MS }),
    { headers: { 'Content-Type': 'application/json' }, tags: { tipo: 'control' } },
  );
  console.log(`[${new Date().toISOString()}] Terceros Open Data -> ${modo} (HTTP ${res.status})`);
}
 
export function iniciarFalla() { fijarModo(MODO_FALLA); }
export function restablecer() { fijarModo('normal'); }
 
export function handleSummary(data) {
  const m = data.metrics;
  const valor = (nombre, campo) => (m[nombre] && m[nombre].values ? m[nombre].values[campo] : null);
  const fases = {};
  for (const fase of ['normal', 'falla', 'recuperacion']) {
    fases[fase] = {
      exito: valor(`checks{fase:${fase}}`, 'rate'),
      p95_ms: valor(`http_req_duration{fase:${fase},tipo:cotizacion}`, 'p(95)'),
      p99_ms: valor(`http_req_duration{fase:${fase},tipo:cotizacion}`, 'p(99)'),
    };
  }
  const resumen = {
    corrida: CORRIDA, modoFalla: MODO_FALLA, faseSegundos: FASE_S, tasaPorMin: TASA_POR_MIN, fases,
    hipotesisCumplida: Object.values(data.metrics)
      .filter((x) => x.thresholds)
      .every((x) => Object.values(x.thresholds).every((t) => t.ok)),
  };
  const texto = JSON.stringify(resumen, null, 2);
  return {
    stdout: `\n===== ${CORRIDA} =====\n${texto}\n`,
    [`resultados/${CORRIDA}-resumen.json`]: texto,
    [`resultados/${CORRIDA}-k6.json`]: JSON.stringify(data, null, 2),
  };
}