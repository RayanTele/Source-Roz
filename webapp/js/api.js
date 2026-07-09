// عميل الـ API — يستهلك واجهتنا فقط. لا اتصال مباشر بـ MRKT إطلاقاً.
import { API_BASE } from "./config.js";
import { platform } from "./platform.js";

export class ApiError extends Error {
  constructor(status, code) { super(code || "error"); this.status = status; this.code = code; }
}

async function request(path, params) {
  const url = new URL(API_BASE + path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    }
  }
  let resp;
  try {
    resp = await fetch(url.toString(), { headers: { ...platform.authHeader() } });
  } catch (e) {
    throw new ApiError(0, "network");
  }
  if (resp.status === 401) throw new ApiError(401, "unauthorized");
  if (resp.status === 503) throw new ApiError(503, "pricing_unavailable");
  if (!resp.ok) throw new ApiError(resp.status, "error");
  return resp.json();
}

export const api = {
  listCollectibles(params) { return request("/api/v1/collectibles", params); },
  getCollectible(id) { return request("/api/v1/collectibles/" + encodeURIComponent(id)); },
  getCategories() { return request("/api/v1/categories"); },
  getFacets() { return request("/api/v1/facets"); },
};
