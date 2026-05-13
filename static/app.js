const { createApp, ref } = Vue;
createApp({
    setup() {
        const slots = ref(Array.from({length: 50}, (_, i) => ({
            id: i < 25 ? `L${i+5}` : `R${i-20}`,
            part: '—',
            done: false
        })));

        const handleFileUpload = async (e) => {
            const formData = new FormData();
            formData.append('file', e.target.files[0]);
            const res = await fetch('/api/upload', { method: 'POST', body: formData });
            const data = await res.json();
            // Обновление карты после загрузки Excel
        };

        return { slots, handleFileUpload };
    }
}).mount('#app');