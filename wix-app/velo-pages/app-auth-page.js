/**
 * Wix Velo Page Code for /app-auth (PUBLIC page — not Members Only)
 *
 * Entry point for "Sign in with livetranslate.net" from the desktop app.
 * Must be public so it runs before Wix forces Members login.
 *
 * Stores redirect_uri + desktop_session in sessionStorage so they survive
 * the Members login redirect (query params are often stripped).
 *
 * SETUP:
 * 1. Create a new page with slug "app-auth"
 * 2. Do NOT set it to Members Only
 * 3. Paste this code in the page's code panel
 * 4. Publish
 */

import wixLocationFrontend from 'wix-location-frontend';

function isValidRedirectUri(uri) {
    try {
        const url = new URL(uri);
        if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') {
            return url.protocol === 'http:';
        }
        if (url.protocol !== 'https:') return false;
        const trusted = [
            'livetranslate.app',
            'www.livetranslate.app',
            'livetranslate.net',
            'www.livetranslate.net',
        ];
        return trusted.some((h) => url.hostname === h || url.hostname.endsWith('.' + h));
    } catch {
        return false;
    }
}

$w.onReady(function () {
    const query = wixLocationFrontend.query;
    const redirectUri = query.redirect_uri;
    const desktopSession = query.desktop_session;

    if (desktopSession && typeof desktopSession === 'string' && desktopSession.length >= 16) {
        try {
            sessionStorage.setItem('live_translate_desktop_session', desktopSession);
        } catch (e) {
            /* ignore */
        }
    }

    if (redirectUri && isValidRedirectUri(redirectUri)) {
        try {
            sessionStorage.setItem('live_translate_redirect_uri', redirectUri);
        } catch (e) {
            /* ignore */
        }
        const params = { redirect_uri: redirectUri };
        if (desktopSession) params.desktop_session = desktopSession;
        const target = '/api-key?' + new URLSearchParams(params).toString();
        wixLocationFrontend.to(target);
        return;
    }

    wixLocationFrontend.to('/api-key');
});
