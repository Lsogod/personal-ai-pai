import { ChatPage } from "./pages/chat/ChatPage";
import { LoginPage } from "./pages/login/LoginPage";
import { AdminPage } from "./pages/admin/AdminPage";
import { useAuthStore } from "./store/auth";

export default function App() {
  const pathname = window.location.pathname || "/";
  if (pathname.startsWith("/admin")) {
    return <AdminPage />;
  }
  const { token } = useAuthStore();
  return token ? <ChatPage /> : <LoginPage />;
}
