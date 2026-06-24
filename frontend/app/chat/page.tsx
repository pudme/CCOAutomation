import { Suspense } from "react";
import { SkeletonCard } from "@/components/shared/LoadingStates";
import { ChatWindow } from "@/components/chat/ChatWindow";

export default function ChatPage() {
  return (
    <div className="h-full">
      <Suspense
        fallback={
          <div className="p-4">
            <SkeletonCard lines={3} />
          </div>
        }
      >
        <ChatWindow />
      </Suspense>
    </div>
  );
}

