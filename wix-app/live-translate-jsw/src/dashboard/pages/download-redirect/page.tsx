import React, { type FC, useState } from 'react';
import { dashboard } from '@wix/dashboard';
import { embeddedScripts } from '@wix/app-management';
import { Box, Button, Card, Page, Text, WixDesignSystemProvider } from '@wix/design-system';
import '@wix/design-system/styles.global.css';

const DownloadRedirectPage: FC = () => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const turnOn = async () => {
    setBusy(true);
    setError(null);
    try {
      await embeddedScripts.embedScript({ parameters: {} });
      dashboard.showToast({ message: 'Returning buyers now go to My Orders.', type: 'success' });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <WixDesignSystemProvider features={{ newColorsBranding: true }}>
      <Page>
        <Page.Header
          title="Download redirect"
          subtitle="Send members who already ordered an app to their order to download it again"
        />
        <Page.Content>
          <Card>
            <Card.Content>
              <Box direction="vertical" gap="SP4">
                <Text>
                  When a signed-in member opens the DubTool or Live Translate product page and already has an order
                  for it, they go straight to My Orders, where the download link is. Turn this on again after every
                  app release.
                </Text>
                {error && <Text skin="error">{error}</Text>}
                <Box>
                  <Button onClick={turnOn} disabled={busy}>
                    {busy ? 'Turning on…' : 'Turn on'}
                  </Button>
                </Box>
              </Box>
            </Card.Content>
          </Card>
        </Page.Content>
      </Page>
    </WixDesignSystemProvider>
  );
};

export default DownloadRedirectPage;
