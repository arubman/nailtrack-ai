import { PublicClientApplication, type AccountInfo } from "@azure/msal-browser";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

interface AuthState {
  ready: boolean;
  devMode: boolean;
  account: AccountInfo | null;
  error: string | null;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  getAccessToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthState | null>(null);

const devMode = (import.meta.env.VITE_AUTH_MODE ?? "dev") !== "entra";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(devMode);
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [msal, setMsal] = useState<PublicClientApplication | null>(null);

  useEffect(() => {
    if (devMode) return;
    const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID;
    const authority = import.meta.env.VITE_ENTRA_AUTHORITY;
    if (!clientId || !authority) {
      setError("Set VITE_ENTRA_CLIENT_ID and VITE_ENTRA_AUTHORITY for Entra sign-in.");
      setReady(true);
      return;
    }
    const app = new PublicClientApplication({
      auth: { clientId, authority, redirectUri: window.location.origin, knownAuthorities: [new URL(authority).host] },
      cache: { cacheLocation: "sessionStorage" },
    });
    setMsal(app);
    void app.initialize().then(async () => {
      const result = await app.handleRedirectPromise();
      const current = result?.account ?? app.getAllAccounts()[0] ?? null;
      if (current) app.setActiveAccount(current);
      setAccount(current);
      setReady(true);
    }).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Sign-in failed");
      setReady(true);
    });
  }, []);

  const value = useMemo<AuthState>(() => ({
    ready,
    devMode,
    account,
    error,
    signIn: async () => {
      if (!msal) return;
      const scope = import.meta.env.VITE_API_SCOPE;
      await msal.loginRedirect({ scopes: scope ? [scope] : ["openid", "profile"] });
    },
    signOut: async () => {
      if (!msal) return;
      await msal.logoutRedirect();
    },
    getAccessToken: async () => {
      if (devMode || !msal) return null;
      const active = msal.getActiveAccount() ?? msal.getAllAccounts()[0];
      if (!active) return null;
      const scope = import.meta.env.VITE_API_SCOPE;
      const result = await msal.acquireTokenSilent({ account: active, scopes: scope ? [scope] : ["openid"] });
      return result.accessToken;
    },
  }), [account, error, msal, ready]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider is missing");
  return value;
}
