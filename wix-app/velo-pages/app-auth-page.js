/**
 * Wix Velo Page Code for /app-auth (PUBLIC page — not Members Only)
 *
 * Entry point for "Sign in with Wix" from the desktop app. This page is public
 * so it loads before any login redirect. It immediately redirects to /api-key
 * with the redirect_uri preserved, maximizing the chance the api-key page
 * receives it after the user logs in.
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
        const trusted = ['livetranslate.app', 'www.livetranslate.app', 'livetranslate.net', 'www.livetranslate.net'];
        return trusted.some(h => url.hostname === h || url.hostname.endsWith('.' + h));
    } catch {
        return false;
    }
}

$w.onReady(function () {
    const query = wixLocation.query;
    const redirectUri = query.redirect_uri;

    if (redirectUri && isValidRedirectUri(redirectUri)) {
        const apiKeyPath = '/api-key'; // Must match your api-key page slug
        const target = `${apiKeyPath}?redirect_uri=${encodeURIComponent(redirectUri)}`;
        wixLocationFrontend.to(target);
    } else {
        wixLocationFrontend.to('/api-key');
    }
});
