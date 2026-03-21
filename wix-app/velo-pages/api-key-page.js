/**
 * Wix Velo Page Code for /account/api-key
 *
 * This code handles the SSO flow for the Live Translate desktop app.
 *
 * SETUP INSTRUCTIONS:
 * 1. Create a new page (slug e.g. api-key → /api-key) in Wix Editor
 * 2. Add a text element with ID "apiKeyText" to display the API key
 * 3. Add a button with ID "copyButton" to copy the key
 * 4. Add a text element with ID "statusText" for status messages
 * 5. Set the page to "Members Only" in page settings
 * 6. Paste this code in the page's code panel
 * 7. Add the backend module (api-key.web.js) to your backend folder
 * 8. Add WIX_SYNC_SECRET to Wix Secrets Manager
 */

import wixLocationFrontend from 'wix-location-frontend';
import { currentMember } from 'wix-members-frontend';
import wixWindowFrontend from 'wix-window-frontend';
import { getApiKeyForMember } from 'backend/api-key.web';

// Trusted redirect hosts (for security)
const TRUSTED_HOSTS = [
    'localhost',
    '127.0.0.1',
    'livetranslate.app',
    'www.livetranslate.app',
    'livetranslate.net',
    'www.livetranslate.net',
];

$w.onReady(async function () {
    try {
        // Show loading state
        $w('#statusText').text = 'Loading your API key...';
        $w('#apiKeyText').hide();
        $w('#copyButton').hide();

        // Get current member
        const member = await currentMember.getMember();

        if (!member || !member.loginEmail) {
            $w('#statusText').text = 'Please log in to view your API key.';
            return;
        }

        const email = member.loginEmail;

        // Get API key from backend (via web module)
        const result = await getApiKeyForMember(email);

        if (!result.success || !result.apiKey) {
            $w('#statusText').text = result.error || 'Could not retrieve API key. Please try again.';
            return;
        }

        const apiKey = result.apiKey;

        // Check for redirect_uri parameter (SSO flow from desktop app)
        const query = wixLocationFrontend.query;
        const redirectUri = query.redirect_uri;

        if (redirectUri && isValidRedirectUri(redirectUri)) {
            // SSO flow: redirect back to desktop app with API key
            $w('#statusText').text = 'Signing you in... You can close this tab.';

            // Build redirect URL with api_key parameter
            const separator = redirectUri.includes('?') ? '&' : '?';
            const finalUrl = `${redirectUri}${separator}api_key=${encodeURIComponent(apiKey)}`;

            // Small delay so user sees the message
            setTimeout(() => {
                wixLocationFrontend.to(finalUrl);
            }, 500);
            return;
        }

        // Normal flow: display API key on page
        $w('#apiKeyText').text = apiKey;
        $w('#apiKeyText').show();
        $w('#copyButton').show();
        $w('#statusText').text = 'Copy this API key to use in the Live Translate app:';

        // Setup copy button
        $w('#copyButton').onClick(() => {
            wixWindowFrontend.copyToClipboard(apiKey);
            $w('#statusText').text = 'API key copied to clipboard!';
        });

    } catch (error) {
        console.error('Error loading API key page:', error);
        $w('#statusText').text = 'An error occurred. Please refresh the page.';
    }
});

/**
 * Validate redirect URI for security
 * Only allow localhost (for desktop app) and trusted domains
 */
function isValidRedirectUri(uri) {
    try {
        const url = new URL(uri);

        // Allow localhost (for desktop app callback)
        if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') {
            // Only allow http for localhost (not https required)
            return url.protocol === 'http:';
        }

        // For other hosts, require https and check against whitelist
        if (url.protocol !== 'https:') {
            return false;
        }

        return TRUSTED_HOSTS.some(host =>
            url.hostname === host || url.hostname.endsWith('.' + host)
        );

    } catch {
        return false;
    }
}
