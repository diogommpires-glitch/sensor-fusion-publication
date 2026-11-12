import { useEffect, useState } from 'react';

export interface Resource<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.text();
    let message = body;
    try {
      const parsed = JSON.parse(body);
      message =
        typeof parsed.detail === 'string' ? parsed.detail : JSON.stringify(parsed.detail ?? parsed);
    } catch {
      // A proxy or unavailable local service may return text rather than API JSON.
    }
    throw new Error(`HTTP ${response.status}: ${message || response.statusText}`);
  }
  return response.json();
}

export function useResource<T>(url: string | null, revision = 0): Resource<T> {
  const [resource, setResource] = useState<Resource<T> & { url: string | null }>({
    url,
    data: null,
    loading: !!url,
    error: null,
  });
  // biome-ignore lint/correctness/useExhaustiveDependencies: revision explicitly invalidates the request for polling and manual refresh.
  useEffect(() => {
    if (!url) {
      setResource({ url, data: null, loading: false, error: null });
      return;
    }
    const controller = new AbortController();
    setResource((previous) => ({
      url,
      data: previous.url === url ? previous.data : null,
      loading: true,
      error: null,
    }));
    request<T>(url, { signal: controller.signal }).then(
      (data) => {
        if (!controller.signal.aborted) setResource({ url, data, loading: false, error: null });
      },
      (error: Error) => {
        if (!controller.signal.aborted)
          setResource({ url, data: null, loading: false, error: error.message });
      },
    );
    return () => controller.abort();
  }, [url, revision]);
  if (resource.url !== url) return { data: null, loading: !!url, error: null };
  return resource;
}
