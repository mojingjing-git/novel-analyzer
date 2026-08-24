import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    // TODO(Task V2): markdown.test.ts 现为 tsx 自运行脚本（无 vitest suite，会被误捕判 FAIL）；
    // V2 将其重写为 vitest 风格后必须删除下面这行 exclude。
    exclude: ['src/utils/markdown.test.ts'],
  },
})
