/**
 * Backend Web Module for API Key Page
 *
 * Place this file in your Wix site's backend folder as: backend/api-key.web.js
 *
 * This module is called from the frontend page to securely get API keys.
 * The WIX_SYNC_SECRET is never exposed to the frontend.
 */

import { Permissions, webMethod } from 'wix-web-module';
import { getSecret } from 'wix-secrets-backend';

// Backend URL
const BACKEND_URL = 'https://api.livetranslate.net';

/**
 * Get API key for the current member
 * @param {string} email - Member's email address
 * @returns {Promise<{success: boolean, apiKey?: string, error?: string}>}
 */
export const getApiKeyForMember = webMethod(
    Permissions.SiteMember,  // Only logged-in members can call this
    async (email) => {
        try {
            // Get secret from Wix Secrets Manager
            const secret = await getSecret('WIX_SYNC_SECRET');

            if (!secret) {
                console.error('WIX_SYNC_SECRET not found in Secrets Manager');
                return { success: false, error: 'Configuration error' };
            }

            const response = await fetch(`${BACKEND_URL}/api/v1/auth/api-key`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Wix-Sync-Secret': secret,
                },
                body: JSON.stringify({ email }),
            });

            if (!response.ok) {
                const errorText = await response.text();
                console.error('Backend error:', response.status, errorText);
                return { success: false, error: `Backend error: ${response.status}` };
            }

            const data = await response.json();
            return {
                success: true,
                apiKey: data.api_key,
                userId: data.user_id,
                tier: data.tier,
            };

        } catch (error) {
            console.error('Error fetching API key:', error);
            return { success: false, error: 'Failed to fetch API key' };
        }
    }
);
