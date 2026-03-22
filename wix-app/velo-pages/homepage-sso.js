/**
 * Wix Velo Code for Homepage (or master page) — Login Bar SSO
 *
 * When the desktop app opens livetranslate.net/?sso_return=..., this stores
 * the return path so when the user clicks "Sign in" in the login bar and lands
 * on /login, the login page knows where to redirect after auth.
 *
 * SETUP:
 * 1. Add this to your homepage's code panel, or to masterPage.js for site-wide.
 * 2. Ensure login-page.js is on your /login page and checks sessionStorage for
 *    live_translate_sso_return_url (see login-page.js).
 * 3. Publish.
 */

import wixLocationFrontend from 'wix-location-frontend';

$w.onReady(function () {
    try {
        const query = wixLocationFrontend.query;
        const ssoReturn = query.sso_return;

        if (ssoReturn && typeof ssoReturn === 'string') {
            const decoded = decodeURIComponent(ssoReturn);
            if (decoded.startsWith('/') && !decoded.startsWith('//')) {
                sessionStorage.setItem('live_translate_sso_return_url', decoded);
            }
        }
    } catch (e) {
        /* ignore */
    }
});
