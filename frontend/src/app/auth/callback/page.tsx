/**
 * app/auth/callback/page.tsx — OAuth callback handler.
 *
 * Swiggy redirects here after user logs in with phone + OTP:
 *   https://soiree-blue.vercel.app/auth/callback?code=...&state=...
 *
 * This page:
 *   1. Reads code + state from URL params
 *   2. Sends them to our backend POST /api/v1/auth/callback
 *   3. Backend exchanges code for access token, stores in Redis
 *   4. Backend returns session_id
 *   5. We store session_id in localStorage
 *   6. Redirect user back to the main page
 *
 * Shows a loading state while the exchange happens.
 * Shows an error if anything goes wrong with a retry link.
 */

'use client'

import { useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

export default function AuthCallback() {
  const router = useRouter()
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

    // Exchange code for token via our backend
    exchangeCode(code, state)
  }, [searchParams])

  async function exchangeCode(code: string, state: string) {
    try {
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL}/api/v1/auth/callback`,
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

      // Store session_id in localStorage
      // This is sent with every plan generation request as X-Session-ID header
      localStorage.setItem('soiree_session_id', data.session_id)
      localStorage.setItem('soiree_expires_at', String(data.expires_at))

      setStatus('success')

      // Redirect back to main page after short delay
      setTimeout(() => router.push('/'), 1500)

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
      {/* Logo */}
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
            width: '40px',
            height: '40px',
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
          <div style={{ fontSize: '32px' }}>✓</div>
          <p style={{ color: '#4d8060', fontSize: '14px' }}>
            Connected to Swiggy successfully
          </p>
          <p style={{ color: 'rgba(242,234,219,0.3)', fontSize: '12px' }}>
            Redirecting you back...
          </p>
        </>
      )}

      {status === 'error' && (
        <>
          <div style={{ fontSize: '32px' }}>✕</div>
          <p style={{ color: '#c95a42', fontSize: '14px' }}>
            {error}
          </p>
          <a href="/" style={{
            padding: '10px 24px',
            border: '1px solid rgba(201,169,110,0.3)',
            borderRadius: '10px',
            color: '#c9a96e',
            fontSize: '13px',
            textDecoration: 'none',
          }}>
            ← Back to Soirée
          </a>
        </>
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </main>
  )
}