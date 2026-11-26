import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import TrialReceiptView from '@/views/TrialReceiptView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 试投回单有独立工作流（勾选帧、逐条审阅、确认/重审、幂等签发），使用专用视图。
    { path: '/trial_receipt', name: 'trial-receipt', component: TrialReceiptView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
