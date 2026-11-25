// 최상위 분기: 초대 링크(#/join/<키>)는 운영자 로그인 없이 JoinView, 나머지는 운영자 앱.
import { useEffect, useState } from "react";
import { inviteFromHash } from "../features/rooms/callText";
import { JoinView } from "../features/rooms/JoinView";
import { App } from "./App";

export function Root() {
  const [invite, setInvite] = useState(() => inviteFromHash());
  useEffect(() => {
    const onHash = () => setInvite(inviteFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return invite ? <JoinView key={invite} invite={invite} /> : <App />;
}
