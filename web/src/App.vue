<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { routes } from './router'
import { useTheme } from './lib/theme'
import BrandMark from './components/BrandMark.vue'
import Icon from './components/Icon.vue'

const route = useRoute()
const { theme, toggleTheme } = useTheme()

const navItems = routes
  .filter((r) => r.meta?.nav && !r.path.includes(':'))
  .map((r) => ({ to: r.path, label: r.meta.nav, icon: r.meta.icon }))

const activeNav = computed(() => route.meta?.nav)
const title = computed(() => route.meta?.title ?? '')

// 管理员配了口令 + TOTP 时，先登录再渲染外壳（文档 §6.5）
const auth = ref({ required: false, authed: true })
const password = ref('')
const code = ref('')
const loginError = ref('')
const loggingIn = ref(false)

async function checkAuth() {
  try {
    const r = await fetch('/api/v1/auth/state')
    if (r.ok) auth.value = await r.json()
  } catch { /* 接口不可达时保持原状，让页面自己报错 */ }
}

async function login() {
  loggingIn.value = true
  loginError.value = ''
  try {
    const r = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ password: password.value, code: code.value }),
    })
    if (!r.ok) {
      loginError.value = (await r.json().catch(() => ({}))).detail || '登录失败'
      return
    }
    auth.value = { ...auth.value, authed: true }
    window.location.reload()
  } catch (e) {
    loginError.value = e.message
  } finally {
    loggingIn.value = false
  }
}

// 演示模式提示：数据由 mock 生成（文档 §8.5）
const demo = ref(false)
onMounted(async () => {
  await checkAuth()
  if (auth.value.required && !auth.value.authed) return
  try {
    const r = await fetch('/api/v1/health')
    if (r.ok) demo.value = Boolean((await r.json()).demo)
  } catch { /* 自检不可达时不挂提示 */ }
})
</script>

<template>
  <div v-if="auth.required && !auth.authed" class="login-wrap">
    <form class="md-card login-card" @submit.prevent="login">
      <BrandMark variant="cloud" />
      <h1>管理员登录</h1>
      <p class="md-muted">口令 + 一次性验证码（TOTP）</p>
      <label class="md-field">
        <span>口令</span>
        <input v-model="password" type="password" autocomplete="current-password" required />
      </label>
      <label class="md-field">
        <span>验证码</span>
        <input v-model="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required />
      </label>
      <p v-if="loginError" class="md-muted" style="color: var(--md-error)">{{ loginError }}</p>
      <button class="md-btn" type="submit" :disabled="loggingIn">
        {{ loggingIn ? '登录中…' : '登录' }}
      </button>
    </form>
  </div>

  <div v-else class="shell">
    <nav class="rail" aria-label="主导航">
      <RouterLink to="/" class="rail-brand" aria-label="Modular Terminal 云节点">
        <BrandMark variant="cloud" />
      </RouterLink>

      <RouterLink
        v-for="item in navItems"
        :key="item.to"
        :to="item.to"
        class="md-rail-item"
        :class="{ 'md-rail-item--active': activeNav === item.label }"
        :aria-current="activeNav === item.label ? 'page' : undefined"
      >
        <Icon :name="item.icon" :filled="activeNav === item.label" />
        <span>{{ item.label }}</span>
      </RouterLink>

    </nav>

    <div class="main">
      <header class="appbar">
        <span class="appbar-title" data-page-title>{{ title }}</span>
        <span v-if="demo" class="md-chip" role="status">演示数据</span>
        <div class="appbar-actions">
          <slot name="appbar"></slot>
          <button
            class="md-btn md-btn--icon theme-toggle"
            :aria-label="theme === 'dark' ? '切到亮色' : '切到暗色'"
            :aria-pressed="theme === 'dark'"
            :title="theme === 'dark' ? '切到亮色' : '切到暗色'"
            @click="toggleTheme"
          >
            <Icon :key="theme" :name="theme === 'dark' ? 'light_mode' : 'dark_mode'" :size="24" />
          </button>
        </div>
      </header>

      <main class="content" tabindex="-1">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: var(--space-5);
  background: var(--md-surface);
}
.login-card {
  width: min(380px, 100%);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-6);
}
.login-card h1 { font: var(--md-headline-small); }
</style>
