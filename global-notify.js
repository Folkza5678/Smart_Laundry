let hasNotifiedWashingComplete = false;

function checkActiveWashingGlobal() {
  fetch('/api/user/active-status')
    .then(res => {
      if (res.status === 401) return null; // ผู้ใช้ยังไม่ได้ล็อกอิน
      return res.json();
    })
    .then(data => {
      if (!data || !data.has_active) return;

      // เมื่อเวลาเหลือ 0 วินาที และยังไม่เคยเด้งป๊อปอัพ
      if (data.seconds_left <= 0 && !hasNotifiedWashingComplete) {
        hasNotifiedWashingComplete = true;

        if (typeof Swal !== 'undefined') {
          Swal.fire({
            title: '🎉 ซักผ้าเสร็จสิ้น!',
            text: `ผ้าของคุณที่ ${data.machine_name} ซักเสร็จเรียบร้อยแล้ว กรุณามารับผ้าด้วยครับ`,
            icon: 'success',
            confirmButtonText: 'รับทราบ',
            confirmButtonColor: '#00036C',
            allowOutsideClick: false
          }).then(() => {
            window.location.href = '/history1.html';
          });
        } else {
          alert(`🎉 ซักผ้าเสร็จสิ้น!\nผ้าของคุณที่ ${data.machine_name} ซักเสร็จเรียบร้อยแล้ว กรุณามารับผ้าด้วยครับ`);
          window.location.href = '/history1.html';
        }
      }
    })
    .catch(err => console.error('Global notify check failed:', err));
}

// ตรวจเช็กเบื้องหลังทุกๆ 5 วินาที
setInterval(checkActiveWashingGlobal, 5000);