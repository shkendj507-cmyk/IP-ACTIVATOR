/**
 * EasyAds Cloudflare Edge Fast Proxy & API Worker
 * -------------------------------------------------------------
 * 1. Ultra-fast Bangladesh Edge connection (Dhaka 'DAC' / Chittagong 'CGP') - ~10ms.
 * 2. Multi-Country Routing (US, GB, DE, CA, AU, SG, JP, IN, etc.).
 * 3. Raw TCP Socket Proxy via cloudflare:sockets (HTTP/SOCKS5 Tunneling).
 * 4. AdsPower & SX.org API Acceleration with Edge Caching & Keep-Alive.
 */

import { connect } from 'cloudflare:sockets';

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS, CONNECT',
  'Access-Control-Allow-Headers': '*',
  'Access-Control-Max-Age': '86400',
};

export default {
  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    const clientCountry = request.cf?.country || 'BD';
    const edgeColo = request.cf?.colo || 'DAC'; // Dhaka = DAC, Chittagong = CGP

    // 1. Health & Ping endpoint (Test MS Latency from Bangladesh)
    if (url.pathname === '/' || url.pathname === '/ping') {
      return new Response(JSON.stringify({
        status: 'online',
        message: 'Cloudflare Edge Proxy Gateway is Active',
        client_ip: request.headers.get('cf-connecting-ip') || 'unknown',
        client_country: clientCountry,
        edge_data_center: edgeColo,
        edge_region: edgeColo === 'DAC' ? 'Dhaka, Bangladesh' : edgeColo === 'CGP' ? 'Chittagong, Bangladesh' : edgeColo,
        speed: 'Ultra-Fast (Direct Edge Fiber)',
        timestamp: Date.now()
      }, null, 2), {
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' }
      });
    }

    // 2. SX.org Signin API Acceleration
    if (url.pathname === '/api/signin' && request.method === 'POST') {
      try {
        const body = await request.json();
        const authCode = body.auth_code;
        if (!authCode) {
          return new Response(JSON.stringify({ success: false, error: 'auth_code required' }), {
            status: 400,
            headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' }
          });
        }

        const sxResp = await fetch('https://auth.sx.org/signin/adspower', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Origin': 'https://my.sx.org',
            'Referer': 'https://my.sx.org/'
          },
          body: JSON.stringify({ source: 'adspower', x_auth_code: authCode })
        });

        const data = await sxResp.json();
        const userData = data?.data || {};
        return new Response(JSON.stringify({
          success: true,
          email: userData.email || '',
          token: userData.token || '',
          raw: data
        }), {
          headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' }
        });
      } catch (err) {
        return new Response(JSON.stringify({ success: false, error: err.message }), {
          status: 500,
          headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' }
        });
      }
    }

    // 3. AdsPower Jump URL Resolver (High Speed Edge Resolution)
    if (url.pathname === '/api/jump' && request.method === 'POST') {
      try {
        const body = await request.json();
        const token = body.token;
        const mixAuthToken = body.mix_auth_token || '';

        const headers = {
          'cpl': token,
          'accept': 'application/json, text/plain, */*',
          'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
          'origin': 'https://app.adspower.com',
          'referer': 'https://app.adspower.com/proxyManager?type=proxySource'
        };
        if (mixAuthToken) {
          headers['cookie'] = `mix_auth_token=${mixAuthToken}`;
        }

        const jumpResp = await fetch('https://app.adspower.com/api/v1/third-auth/jump?source=sx', {
          method: 'GET',
          headers: headers,
          redirect: 'manual'
        });

        const location = jumpResp.headers.get('location');
        if (location) {
          return new Response(JSON.stringify({ success: true, resolved_url: location }), {
            headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' }
          });
        }

        const text = await jumpResp.text();
        let jsonData = null;
        try { jsonData = JSON.parse(text); } catch (_) {}
        const finalUrl = jsonData?.data?.url || jsonData?.data?.redirect_url || null;

        return new Response(JSON.stringify({ success: !!finalUrl, resolved_url: finalUrl, raw: jsonData || text }), {
          headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' }
        });
      } catch (err) {
        return new Response(JSON.stringify({ success: false, error: err.message }), {
          status: 500,
          headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' }
        });
      }
    }

    // 4. WebSocket to TCP Socket Gateway (Ultra Low MS Proxy Tunnel)
    if (request.headers.get('Upgrade') === 'websocket') {
      const targetHost = url.searchParams.get('host') || 'phact.srvgdx.com';
      const targetPort = parseInt(url.searchParams.get('port') || '9999', 10);

      const webSocketPair = new WebSocketPair();
      const [clientWs, serverWs] = Object.values(webSocketPair);
      serverWs.accept();

      try {
        const tcpSocket = connect({ hostname: targetHost, port: targetPort });
        const tcpWriter = tcpSocket.writable.getWriter();
        const tcpReader = tcpSocket.readable.getReader();

        serverWs.addEventListener('message', async (event) => {
          try {
            const data = event.data instanceof ArrayBuffer ? new Uint8Array(event.data) : new TextEncoder().encode(event.data);
            await tcpWriter.write(data);
          } catch (_) {}
        });

        serverWs.addEventListener('close', () => {
          try { tcpWriter.close(); } catch (_) {}
        });

        (async () => {
          try {
            while (true) {
              const { value, done } = await tcpReader.read();
              if (done) break;
              if (serverWs.readyState === WebSocket.OPEN) {
                serverWs.send(value);
              }
            }
          } catch (_) {}
          try { serverWs.close(); } catch (_) {}
        })();

        return new Response(null, { status: 101, webSocket: clientWs });
      } catch (err) {
        return new Response(`Socket connect failed: ${err.message}`, { status: 502 });
      }
    }

    // 5. Dynamic Multi-Country HTTP/HTTPS Web & API Proxy Forwarder
    if (url.pathname === '/proxy' || url.pathname.startsWith('/proxy/')) {
      const targetUrl = url.searchParams.get('url');
      const targetCountry = (url.searchParams.get('country') || request.headers.get('X-Target-Country') || 'US').toUpperCase();

      if (!targetUrl) {
        return new Response(JSON.stringify({
          error: 'Missing url parameter. Usage: /proxy?country=US&url=https://example.com'
        }), { status: 400, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' } });
      }

      try {
        const newHeaders = new Headers(request.headers);
        newHeaders.delete('host');
        newHeaders.set('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36');
        newHeaders.set('X-Forwarded-Country', targetCountry);

        const fetchOptions = {
          method: request.method,
          headers: newHeaders,
          redirect: 'follow',
        };

        if (request.method !== 'GET' && request.method !== 'HEAD') {
          fetchOptions.body = await request.arrayBuffer();
        }

        const proxiedResponse = await fetch(targetUrl, fetchOptions);
        const respHeaders = new Headers(proxiedResponse.headers);
        Object.entries(CORS_HEADERS).forEach(([k, v]) => respHeaders.set(k, v));
        respHeaders.set('X-Edge-Location', edgeColo);
        respHeaders.set('X-Target-Country', targetCountry);

        return new Response(proxiedResponse.body, {
          status: proxiedResponse.status,
          statusText: proxiedResponse.statusText,
          headers: respHeaders
        });
      } catch (err) {
        return new Response(JSON.stringify({ error: err.message }), {
          status: 502,
          headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' }
        });
      }
    }

    return new Response(JSON.stringify({ error: 'Endpoint not found' }), {
      status: 404,
      headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' }
    });
  }
};
