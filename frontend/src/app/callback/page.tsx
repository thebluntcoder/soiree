'use client'
import { useEffect } from 'react'
import { useRouter } from 'next/navigation'

export default function CallbackRedirect() {
  const router = useRouter()
  useEffect(() => {
    router.replace('/auth/callback' + window.location.search)
  }, [])
  return null
}