/**
 * app/auth/callback/page.tsx — OAuth callback handler.
 * Wrapped in Suspense as required by Next.js 14 for useSearchParams().
 */

'use client'

import { Suspense } from 'react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'next/navigation'

function CallbackHandler() {
  const searchParams = useSearchParams()
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading')
  const [error, setError] = useState<string>('')

  useEffect(() => {
    const code = searchParams.get('code')
    const state = searchParams.get('state')
    const errorParam = searchParams.get('error')

    if (errorParam) {
      setError(`Swiggy returned an error: ${errorParam}`)
      setStatus('error')
      return
    }

    if (!code || !state) {
      setError('Missing code or state in callback URL')
      setStatus('error')
      return
    }

    exchangeCode(code, state)
  }, [searchParams])

  async function exchangeCode(code: string, state: string) {
    try {
        const apiBase = typeof window !== 'undefined' && window.location.hostname === 'localhost'
        ? 'http://localhost:8000'
        : (process.env.NEXT_PUBLIC_API_URL || 'https://soiree-production.up.railway.app');

      const response = await fetch(
        `${apiBase}/api/v1/auth/callback`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code, state }),
        }
      )

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'Token exchange failed')
      }

      const data = await response.json()
      localStorage.setItem('soiree_session_id', data.session_id)
      localStorage.setItem('soiree_expires_at', String(data.expires_at))
      setStatus('success')

      // Return to the page that started the flow (e.g. /demo.html), not the
      // app root. Same-origin path only. Full navigation — /demo.html is a
      // static file, not a Next.js route.
      let returnTo = localStorage.getItem('soiree_return') || '/'
      localStorage.removeItem('soiree_return')
      if (!returnTo.startsWith('/') || returnTo.startsWith('//')) returnTo = '/'
      setTimeout(() => { window.location.href = returnTo }, 1200)

    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      setError(message)
      setStatus('error')
    }
  }

  return (
    <main style={{
      minHeight: '100vh',
      background: '#0b0907',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: '24px',
      fontFamily: 'DM Sans, sans-serif',
      color: '#f2eadb',
    }}>
      <div style={{
        fontFamily: 'Cormorant Garamond, serif',
        fontSize: '32px',
        letterSpacing: '8px',
        background: 'linear-gradient(135deg, #c9a96e, #f5dfa0)',
        WebkitBackgroundClip: 'text',
        WebkitTextFillColor: 'transparent',
      }}>
        SOIRÉE
      </div>

      {status === 'loading' && (
        <>
          <div style={{
            width: '40px', height: '40px',
            border: '2px solid rgba(201,169,110,0.2)',
            borderTopColor: '#c9a96e',
            borderRadius: '50%',
            animation: 'spin 0.8s linear infinite',
          }} />
          <p style={{ color: 'rgba(242,234,219,0.5)', fontSize: '14px' }}>
            Connecting your Swiggy account...
          </p>
        </>
      )}

      {status === 'success' && (
        <>
          <div style={{ fontSize: '32px', color: '#4d8060' }}>✓</div>
          <p style={{ color: '#4d8060', fontSize: '14px' }}>Connected to Swiggy successfully</p>
          <p style={{ color: 'rgba(242,234,219,0.3)', fontSize: '12px' }}>Redirecting you back...</p>
        </>
      )}

      {status === 'error' && (
        <>
          <div style={{ fontSize: '32px', color: '#c95a42' }}>✕</div>
          <p style={{ color: '#c95a42', fontSize: '14px' }}>{error}</p>
          <a href="/" style={{
            padding: '10px 24px',
            border: '1px solid rgba(201,169,110,0.3)',
            borderRadius: '10px',
            color: '#c9a96e',
            fontSize: '13px',
            textDecoration: 'none',
          }}>← Back to Soirée</a>
        </>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </main>
  )
}

// Suspense wrapper required by Next.js 14 for useSearchParams()
export default function AuthCallback() {
  return (
    <Suspense fallback={
      <main style={{
        minHeight: '100vh', background: '#0b0907',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <div style={{
          fontFamily: 'Cormorant Garamond, serif', fontSize: '32px',
          letterSpacing: '8px', color: '#c9a96e',
        }}>SOIRÉE</div>
      </main>
    }>
      <CallbackHandler />
    </Suspense>
  )
}