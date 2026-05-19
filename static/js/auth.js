// Get token from localStorage.
function getToken() {
  return localStorage.getItem('access_token')
}

// Check if logged in.
function isLoggedIn() {
  return !!localStorage.getItem('access_token')
}

// Check if admin.
function isAdmin() {
  return localStorage.getItem('is_staff') === 'true'
}

// Logout function.
function logout() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  localStorage.removeItem('is_staff')
  localStorage.removeItem('username')
  window.location.href = '/login/'
}

// Auth headers for fetch.
function authHeaders() {
  return {
    'Authorization': 'Bearer ' + getToken(),
    'Content-Type': 'application/json',
  }
}

// Guard for normal user pages.
function requireAuth() {
  if (!isLoggedIn()) {
    window.location.href = '/login/'
  }
}

// Guard for admin pages.
function requireAdmin() {
  if (!isLoggedIn() || !isAdmin()) {
    window.location.href = '/login/'
  }
}

window.getToken = getToken
window.isLoggedIn = isLoggedIn
window.isAdmin = isAdmin
window.logout = logout
window.authHeaders = authHeaders
window.requireAuth = requireAuth
window.requireAdmin = requireAdmin