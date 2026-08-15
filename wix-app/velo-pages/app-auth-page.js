/**
 * Wix Velo Page Code for /app-auth (PUBLIC — not Members Only)
 *
 * Desktop SSO entry: persist session_id + redirect_uri across Wix login
 * (query params are often stripped), then forward to /api-key.
 */

import wixLocationFrontend from 'wix-location-frontend';

function isValidRedirectUri(uri) {
    try {
        const url = new URL(uri);
        if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') {
            return url.protocol === 'http:';
        }
        if (url.protocol !== 'https:') return false;
        const trusted = ['livetranslate.app', 'www.livetranslate.app', 'livetranslate.net', 'www.livetranslate.net'];
        return trusted.some(h => url.hostname === h || url.hostname.endsWith('.' + h));
    } catch {
        return false;
    }
}

$w.onReady(function () {
    const query = wixLocationFrontend.query;
    const redirectUri = query.redirect_uri;
    const sessionId = query.session_id;
    const apiKeyPath = '/api-key';

    // Survive Members login redirect (query string is often dropped)
    if (sessionId && typeof sessionId === 'string') {
        try { sessionStorage.setItem('live_translate_session_id', sessionId); } catch (e) { /* ignore */ }
    }
    if (redirectUri && isValidRedirectUri(redirectUri)) {
        try { sessionStorage.setItem('live_translate_redirect_uri', redirectUri); } catch (e) { /* ignore */ }
    }

    const params = [];
    if (sessionId) params.push('session_id=' + encodeURIComponent(sessionId));
    if (redirectUri && isValidRedirectUri(redirectUri)) {
        params.push('redirect_uri=' + encodeURIComponent(redirectUri));
    }

    const target = params.length ? `${apiKeyPath}?${params.join('&')}` : apiKeyPath;
    wixLocationFrontend.to(target);
});
