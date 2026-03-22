/**
 * Wix Velo Page Code for /login
 *
 * Enables the returnUrl query parameter so the desktop app's "Sign in with Wix"
 * flow redirects users to /api-key after login, completing the SSO callback.
 *
 * SETUP:
 * 1. Create or use your existing Members Login page (slug /login)
 * 2. Add a Login element if not already present
 * 3. Paste this code in the page's code panel (or in masterPage.js for site-wide)
 */

import { authentication } from 'wix-members-frontend';
import wixLocationFrontend from 'wix-location-frontend';

$w.onReady(function () {
    // returnUrl from query (direct /login?returnUrl=...) or from sessionStorage (homepage sso_return)
    const query = wixLocationFrontend.query;
    let returnUrl = query.returnUrl;
    if (!returnUrl && typeof sessionStorage !== 'undefined') {
        returnUrl = sessionStorage.getItem('live_translate_sso_return_url');
        if (returnUrl) sessionStorage.removeItem('live_translate_sso_return_url');
    }

    if (returnUrl && isValidReturnUrl(returnUrl)) {
        authentication.onLogin(() => {
            wixLocationFrontend.to(returnUrl);
        });
    }
});

/**
 * Only allow same-origin relative paths to prevent open redirects.
 */
function isValidReturnUrl(url) {
    if (typeof url !== 'string') return false;
    const decoded = decodeURIComponent(url);
    return decoded.startsWith('/') && !decoded.startsWith('//');
}
