// app/page.tsx — route: /
//
// This Next.js app only exists to host the Swiggy OAuth callback (see
// app/auth/callback/ and app/callback/ — both are whitelisted redirect
// URIs with Swiggy and must keep working). demo.html is the actual
// frontend; send anyone who lands on the bare domain there.

import { redirect } from 'next/navigation'

export default function Home() {
  redirect('/demo.html')
}
