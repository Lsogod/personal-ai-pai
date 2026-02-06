import { ChatPage } from "./pages/chat/ChatPage";
import { LoginPage } from "./pages/login/LoginPage";
import { useAuthStore } from "./store/auth";

export default function App() {
  const { token } = useAuthStore();
  return token ? <ChatPage /> : <LoginPage />;
}
