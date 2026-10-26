// global-notify.js - ระบบตรวจจับและแจ้งเตือนสถานะการซักเสร็จแบบ Global
(function () {
  let activeCheckInterval = null;

  async function checkActiveWashingStatus() {
    try {
      const res = await fetch('/api/user/active-status');
      if (!res.ok) return;
      const data = await res.json();

      // เมื่อมีตู้ที่เพิ่งซักเสร็จสิ้น (status = 'just_completed')
      if (data.has_active && data.status === 'just_completed') {
        const notifiedKey = 'notified_completed_' + data.booking_id;

        // เช็กว่าเคยแสดง Popup ซักเสร็จรายการนี้ไปแล้วหรือยัง (ป้องกันแสดงซ้ำ)
        if (!sessionStorage.getItem(notifiedKey)) {
          sessionStorage.setItem(notifiedKey, 'true');

          // ลบเวลาจองค้างออก
          localStorage.removeItem('booking_expires_at');

          Swal.fire({
            icon: 'success',
            title: '🧺 ผ้าซักเสร็จเรียบร้อยแล้ว!',
            text: `ผ้าของคุณที่ตู้ ${data.machine_name} (${data.machine_code}) ซักเสร็จเรียบร้อยแล้ว กรุณามารับผ้าด้วยครับ`,
            confirmButtonText: 'ไปที่หน้าประวัติ',
            confirmButtonColor: '#00036C',
            allowOutsideClick: false
          }).then((result) => {
            if (result.isConfirmed) {
              window.location.href = 'history1.html';
            }
          });
        }
      }
    } catch (e) {
      console.error('Error checking active status:', e);
    }
  }

  // เริ่มทำงานทันทีที่โหลดหน้าเว็บ และตรวจสอบทุกๆ 5 วินาที
  document.addEventListener('DOMContentLoaded', () => {
    checkActiveWashingStatus();
    activeCheckInterval = setInterval(checkActiveWashingStatus, 5000);
  });
})();