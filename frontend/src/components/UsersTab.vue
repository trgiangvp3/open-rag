<script setup lang="ts">
defineOptions({ name: 'UsersTab' })
import { ref, onMounted } from 'vue'
import { listUsers, createUser, changeUserPassword, deleteUser, type UserInfo } from '../api'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const users = ref<UserInfo[]>([])
const loading = ref(false)
const msg = ref('')
const msgError = ref(false)

// Create form
const showCreate = ref(false)
const form = ref({ username: '', password: '', displayName: '', role: 'user' })
const creating = ref(false)

// Change password
const pwUserId = ref<number | null>(null)
const newPassword = ref('')
const changingPw = ref(false)

// Delete confirm
const deleteId = ref<number | null>(null)

async function load() {
  loading.value = true
  try {
    const { data } = await listUsers()
    users.value = data
  } catch {
    flash('Lỗi khi tải danh sách người dùng', true)
  } finally {
    loading.value = false
  }
}

function flash(text: string, error = false) {
  msg.value = text
  msgError.value = error
  setTimeout(() => { msg.value = '' }, 3000)
}

async function onCreate() {
  if (!form.value.username || !form.value.password || !form.value.displayName) return
  creating.value = true
  try {
    await createUser(form.value.username, form.value.password, form.value.displayName, form.value.role)
    showCreate.value = false
    form.value = { username: '', password: '', displayName: '', role: 'user' }
    flash('Tạo người dùng thành công')
    await load()
  } catch (e: any) {
    flash(e.response?.data?.message ?? 'Lỗi khi tạo người dùng', true)
  } finally {
    creating.value = false
  }
}

async function onChangePassword() {
  if (!pwUserId.value || !newPassword.value) return
  changingPw.value = true
  try {
    await changeUserPassword(pwUserId.value, newPassword.value)
    pwUserId.value = null
    newPassword.value = ''
    flash('Đổi mật khẩu thành công')
  } catch (e: any) {
    flash(e.response?.data?.message ?? 'Lỗi khi đổi mật khẩu', true)
  } finally {
    changingPw.value = false
  }
}

async function onDelete() {
  if (!deleteId.value) return
  try {
    await deleteUser(deleteId.value)
    deleteId.value = null
    flash('Xoá người dùng thành công')
    await load()
  } catch (e: any) {
    flash(e.response?.data?.message ?? 'Lỗi khi xoá người dùng', true)
  }
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

onMounted(load)
</script>

<template>
  <div class="max-w-4xl mx-auto space-y-6">

    <!-- Header -->
    <div class="flex items-center justify-between">
      <div>
        <h2 class="text-lg font-semibold th-text">Quản lý người dùng</h2>
        <p class="th-text3 text-sm">Tạo, chỉnh sửa và quản lý tài khoản người dùng trong hệ thống.</p>
      </div>
      <div class="flex items-center gap-3">
        <span v-if="msg" class="text-sm" :class="msgError ? 'text-red-400' : 'text-green-400'">{{ msg }}</span>
        <button @click="showCreate = true"
          class="px-4 py-2 th-btn hover:th-btn rounded-lg text-white text-sm font-medium transition-colors">
          + Thêm người dùng
        </button>
      </div>
    </div>

    <!-- Create dialog -->
    <div v-if="showCreate" class="th-elevated border th-border rounded-xl p-5 space-y-4">
      <h3 class="th-text text-sm font-semibold">Tạo người dùng mới</h3>
      <div class="grid grid-cols-2 gap-4">
        <div class="space-y-1.5">
          <label class="th-text2 text-xs">Tên đăng nhập</label>
          <input v-model="form.username" type="text" placeholder="username"
            class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text text-sm focus:outline-none" />
        </div>
        <div class="space-y-1.5">
          <label class="th-text2 text-xs">Mật khẩu</label>
          <input v-model="form.password" type="password" placeholder="6-72 ký tự"
            class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text text-sm focus:outline-none" />
        </div>
        <div class="space-y-1.5">
          <label class="th-text2 text-xs">Tên hiển thị</label>
          <input v-model="form.displayName" type="text" placeholder="Nguyễn Văn A"
            class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text text-sm focus:outline-none" />
        </div>
        <div class="space-y-1.5">
          <label class="th-text2 text-xs">Vai trò</label>
          <select v-model="form.role"
            class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text text-sm focus:outline-none">
            <option value="user">Người dùng</option>
            <option value="admin">Quản trị viên</option>
          </select>
        </div>
      </div>
      <div class="flex justify-end gap-2 pt-2">
        <button @click="showCreate = false"
          class="px-4 py-2 th-bg3 border th-border rounded-lg th-text text-sm hover:th-hover transition-colors">
          Huỷ
        </button>
        <button @click="onCreate" :disabled="creating || !form.username || !form.password || !form.displayName"
          class="px-4 py-2 th-btn hover:th-btn disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors">
          {{ creating ? 'Đang tạo...' : 'Tạo' }}
        </button>
      </div>
    </div>

    <!-- Users table -->
    <div class="th-elevated border th-border rounded-xl overflow-hidden">
      <div v-if="loading" class="p-8 text-center th-text3 text-sm">Đang tải...</div>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="th-bg3 text-left">
            <th class="px-4 py-3 th-text2 font-medium">Người dùng</th>
            <th class="px-4 py-3 th-text2 font-medium">Vai trò</th>
            <th class="px-4 py-3 th-text2 font-medium">Ngày tạo</th>
            <th class="px-4 py-3 th-text2 font-medium text-right">Thao tác</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id" class="border-t th-border hover:th-hover transition-colors">
            <td class="px-4 py-3">
              <div class="flex items-center gap-3">
                <div class="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
                  style="background: var(--accent-light); color: var(--text-accent)">
                  {{ u.displayName?.[0]?.toUpperCase() || 'U' }}
                </div>
                <div>
                  <p class="th-text font-medium">{{ u.displayName }}</p>
                  <p class="th-text3 text-xs">{{ u.username }}</p>
                </div>
              </div>
            </td>
            <td class="px-4 py-3">
              <span :class="[
                'inline-block px-2 py-0.5 rounded-full text-xs font-medium',
                u.role === 'admin' ? 'bg-amber-500/15 text-amber-400' : 'bg-blue-500/15 text-blue-400'
              ]">
                {{ u.role === 'admin' ? 'Quản trị viên' : 'Người dùng' }}
              </span>
            </td>
            <td class="px-4 py-3 th-text3">{{ formatDate(u.createdAt) }}</td>
            <td class="px-4 py-3 text-right">
              <div class="flex items-center justify-end gap-1">
                <!-- Change password -->
                <button @click="pwUserId = u.id; newPassword = ''"
                  class="px-2 py-1 rounded th-text3 hover:th-text text-xs transition-colors"
                  title="Đổi mật khẩu">
                  Đổi MK
                </button>
                <!-- Delete (not self) -->
                <button v-if="u.id !== auth.user?.id" @click="deleteId = u.id"
                  class="px-2 py-1 rounded text-red-400 hover:text-red-300 text-xs transition-colors"
                  title="Xoá người dùng">
                  Xoá
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="!users.length && !loading">
            <td colspan="4" class="px-4 py-8 text-center th-text3">Chưa có người dùng nào.</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Change password dialog -->
    <Teleport to="body">
      <div v-if="pwUserId !== null" class="fixed inset-0 z-50 flex items-center justify-center"
        style="background: rgba(0,0,0,0.5)" @click.self="pwUserId = null">
        <div class="th-elevated border th-border rounded-xl p-5 w-96 space-y-4">
          <h3 class="th-text text-sm font-semibold">
            Đổi mật khẩu — {{ users.find(u => u.id === pwUserId)?.displayName }}
          </h3>
          <div class="space-y-1.5">
            <label class="th-text2 text-xs">Mật khẩu mới</label>
            <input v-model="newPassword" type="password" placeholder="6-72 ký tự" autofocus
              @keyup.enter="onChangePassword"
              class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text text-sm focus:outline-none" />
          </div>
          <div class="flex justify-end gap-2">
            <button @click="pwUserId = null"
              class="px-4 py-2 th-bg3 border th-border rounded-lg th-text text-sm hover:th-hover transition-colors">
              Huỷ
            </button>
            <button @click="onChangePassword" :disabled="changingPw || newPassword.length < 6"
              class="px-4 py-2 th-btn hover:th-btn disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors">
              {{ changingPw ? 'Đang lưu...' : 'Đổi mật khẩu' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Delete confirm dialog -->
    <Teleport to="body">
      <div v-if="deleteId !== null" class="fixed inset-0 z-50 flex items-center justify-center"
        style="background: rgba(0,0,0,0.5)" @click.self="deleteId = null">
        <div class="th-elevated border th-border rounded-xl p-5 w-96 space-y-4">
          <h3 class="th-text text-sm font-semibold">Xác nhận xoá</h3>
          <p class="th-text2 text-sm">
            Bạn có chắc muốn xoá người dùng
            <strong>{{ users.find(u => u.id === deleteId)?.displayName }}</strong>?
            Thao tác này không thể hoàn tác.
          </p>
          <div class="flex justify-end gap-2">
            <button @click="deleteId = null"
              class="px-4 py-2 th-bg3 border th-border rounded-lg th-text text-sm hover:th-hover transition-colors">
              Huỷ
            </button>
            <button @click="onDelete"
              class="px-4 py-2 bg-red-600 hover:bg-red-500 rounded-lg text-white text-sm font-medium transition-colors">
              Xoá
            </button>
          </div>
        </div>
      </div>
    </Teleport>

  </div>
</template>
