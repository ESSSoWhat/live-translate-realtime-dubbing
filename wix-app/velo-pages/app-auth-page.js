/**
 * Wix Velo Page Code for /app-auth (PUBLIC page — not Members Only)
 *
 * Entry point for "Sign in with livetranslate.net" from the desktop app.
 * Must be public so it runs before Wix forces Members login.
 *
 * Critical: store redirect_uri in sessionStorage HERE. If we only put it on
 * /api-key?redirect_uri=..., Wix login often strips the query before api-key
 * JS runs — then the desktop app never gets the callback.
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

    if (redirectUri && isValidRedirectUri(redirectUri)) {
        try {
            sessionStorage.setItem('live_translate_redirect_uri', redirectUri);
        } catch (e) {
            /* ignore */
        }
        // Also pass query for the rare case Members page loads without stripping it
        const target =
            '/api-key?' +
            new URLSearchParams({ redirect_uri: redirectUri }).toString();
        wixLocationFrontend.to(target);
        return;
    }

    wixLocationFrontend.to('/api-key');
});
