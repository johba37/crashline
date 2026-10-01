import type { Theme } from '@rainbow-me/rainbowkit'

// Every value points at a CSS variable from theme.css, so this one theme follows
// both the dark and the light app theme.
export const rainbowKitTheme: Theme = {
  blurs: {
    modalOverlay: 'blur(8px)',
  },
  colors: {
    accentColor: 'var(--color-accent)',
    accentColorForeground: 'var(--color-on-accent)',
    actionButtonBorder: 'var(--color-line)',
    actionButtonBorderMobile: 'var(--color-line)',
    actionButtonSecondaryBackground: 'var(--color-surface-well)',
    closeButton: 'var(--color-ink-muted)',
    closeButtonBackground: 'var(--color-surface-well)',
    connectButtonBackground: 'var(--color-glass-fill)',
    connectButtonBackgroundError: 'var(--color-abort-soft)',
    connectButtonInnerBackground: 'var(--color-surface-overlay)',
    connectButtonText: 'var(--color-ink)',
    connectButtonTextError: 'var(--color-abort)',
    connectionIndicator: 'var(--color-go)',
    downloadBottomCardBackground: 'var(--color-surface-well)',
    downloadTopCardBackground: 'var(--color-surface-raised)',
    error: 'var(--color-abort)',
    generalBorder: 'var(--color-line)',
    generalBorderDim: 'var(--color-line)',
    menuItemBackground: 'var(--color-surface-well)',
    modalBackdrop: 'var(--color-scrim)',
    modalBackground: 'var(--color-surface-overlay)',
    modalBorder: 'var(--color-line)',
    modalText: 'var(--color-ink)',
    modalTextDim: 'var(--color-ink-faint)',
    modalTextSecondary: 'var(--color-ink-muted)',
    profileAction: 'var(--color-surface-well)',
    profileActionHover: 'var(--color-surface)',
    profileForeground: 'var(--color-surface-overlay)',
    selectedOptionBorder: 'var(--color-accent)',
    standby: 'var(--color-hold)',
  },
  fonts: {
    body: 'var(--font-sans)',
  },
  radii: {
    actionButton: '9999px',
    connectButton: '9999px',
    menuButton: '9999px',
    modal: 'var(--radius-xl)',
    modalMobile: 'var(--radius-xl)',
  },
  shadows: {
    connectButton: 'var(--elevation-raised)',
    dialog: 'var(--elevation-glass)',
    profileDetailsAction: 'var(--elevation-raised)',
    selectedOption: 'var(--elevation-raised)',
    selectedWallet: 'var(--elevation-raised)',
    walletLogo: 'var(--elevation-raised)',
  },
}
