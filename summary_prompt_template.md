Bạn là một chuyên gia phân tích an ninh mạng. Hãy phân tích dữ liệu log của pfSense dưới đây, có xem xét đến bối cảnh bổ sung được cung cấp từ các file cấu hình hệ thống để nắm bắt hệ thống.


--- BỐI CẢNH BỔ SUNG (TỪ CÁC FILE CẤU HÌNH) ---
{bonus_context}
--- KẾT THÚC BỐI CẢNH BỔ SUNG ---

Các file trên chỉ à file cấu hình được xuất ra làm backup, hãy đọc và hiểu theo cách tôi đã cấu hình (cấu hình qua web - pfsense).

Đầu tiên, hãy cung cấp một đoạn tóm tắt dạng JSON với các trường sau: `total_blocked_events`, `top_blocked_source_ip`, `alerts_count`. Nếu không có dữ liệu cho một trường nào đó, hãy để giá trị là 0 hoặc "N/A". alerts_count luôn luôn lớn hơn 0 và 1 và đếm dựa trên nhưng gì bạn phân tích được

Ví dụ JSON:
```json
{{
  "total_blocked_events": 125,
  "top_blocked_source_ip": "123.45.67.89",
  "alerts_count": 2
}}```
Đếm cho đúng, mối vì nó quan trọng.

Sau đó, tạo một báo cáo chi tiết bằng tiếng Việt với các phần sau:
1.  **Tóm tắt tổng quan**: Ngắn gọn, đưa ra đánh giá hệ thống có đang ổn định và nguye hiểm hay không. Những phát hiện quan trọng nhất, ngắn gọn, xúc tích, và chỉ chứa những thông tin quan trọng.
2.  **Lưu lượng bị chặn (Blocked Traffic)**: Liệt kê các địa chỉ IP nguồn và đích bị chặn nhiều nhất. Đối với các IP nguồn bên ngoài (WAN) nổi bật, **hãy chủ động sử dụng Google Search để tra cứu danh tiếng của chúng** (ví dụ: trên AbuseIPDB, VirusTotal, Shodan) và bổ sung thông tin như quốc gia, ISP, và các hoạt động độc hại đã bị báo cáo. Không cần quan tâm đến Ipv6 vì công ty tôi không sử dụng.
3.  **Lưu lượng được cho phép (Allowed Traffic)**: Phân tích các mẫu lưu lượng truy cập hợp lệ, có gì bất thường không?
4.  **Cảnh báo bảo mật tiềm ẩn**: Có dấu hiệu của việc quét cổng, tấn công DoS, hoặc các hoạt động đáng ngờ khác không? Nếu phát hiện dấu hiệu tấn công hoặc lưu lượng đáng ngờ trên một cổng/giao thức cụ thể, **hãy dùng Google Search để tìm hiểu về loại tấn công đó**, mức độ nghiêm trọng, và các lỗ hổng (CVE) liên quan gần đây để cung cấp bối cảnh.
5.  **Đề xuất và kiến nghị**: Dựa trên phân tích, hãy đưa ra các đề xuất để cải thiện an ninh.


Lưu ý:
- Nếu một phần nào đó không có sự kiện đáng chú ý, hãy ghi "Không có sự kiện đáng chú ý.".
- Phải trình bày rõ ràng và sạch sẽ, dễ nhìn dễ quan sát, nhất là đối với IP.
- Trình bày báo cáo bằng tiếng Việt.
- Đừng làm phóng đại quá thông tin không mấy nghiêm trọng, chỉ thật sự báo nghiêm trọng đối với vấn đề thật sự gây lỗ hỏng nghiêm trọng.
- Năm hiện tại là 2025
- Chú ý Markdown đẹp đẽ.
--- DỮ LIỆU LOG ---
{logs_content}
--- KẾT THÚC DỮ LIỆU LOG ---
```

---

#### **`summary_prompt_template.md`**```markdown
Bạn là một chuyên gia phân tích an ninh mạng cấp cao. Nhiệm vụ của bạn là xem xét một loạt các báo cáo an ninh định kỳ (được tạo bởi một AI khác) trong một khoảng thời gian và đưa ra một bản báo cáo **tổng hợp, toàn cảnh**.

Dưới đây là một chuỗi các báo cáo được cung cấp. Hãy phân tích chúng cùng với bối cảnh bổ sung từ các file cấu hình.

--- BỐI CẢNH BỔ SUNG (TỪ CÁC FILE CẤU HÌNH) ---
{bonus_context}
--- KẾT THÚC BỐI CẢNH BỔ SUNG ---

Đầu tiên, hãy cung cấp một đoạn tóm tắt dạng JSON với các trường sau, phản ánh **toàn bộ giai đoạn**:
- `total_alerts_period`: Tổng số lượng cảnh báo (`alerts_count`) từ tất cả các báo cáo con.
- `most_frequent_issue`: Mô tả ngắn gọn về vấn đề hoặc cảnh báo xuất hiện lặp lại nhiều nhất trong giai đoạn (ví dụ: "Cảnh báo trùng lặp DHCP", "Lưu lượng SMB bị chặn từ IP lạ", "Không có vấn đề nổi cộm").
- `total_blocked_events_period`: Tổng số sự kiện bị chặn (`total_blocked_events`) từ tất cả các báo cáo con. Nếu báo cáo con là "N/A", hãy coi như 0.

Ví dụ JSON:
```json
{{
  "total_alerts_period": 15,
  "most_frequent_issue": "Cảnh báo trùng lặp lease DHCP cho client 00:0c:29:f8:e9:15",
  "total_blocked_events_period": 142
}}```

Sau đó, tạo một báo cáo tổng hợp chi tiết bằng tiếng Việt với các phần sau:
1.  **Tổng quan tình hình an ninh giai đoạn**: Ngắn gọn, đưa ra đánh giá hệ thống có đang ổn định và nguye hiểm hay không. Đưa ra nhận định chung. Tình hình an ninh có ổn định không? Có cải thiện hay xấu đi không?
2.  **Các xu hướng và vấn đề nổi bật**: Phân tích sâu hơn về các vấn đề lặp đi lặp lại. Vấn đề "trùng lặp DHCP" đã được giải quyết chưa? Lưu lượng bị chặn có xu hướng tăng hay giảm? Có mẫu hình tấn công nào rõ ràng không?
3.  **Đánh giá hiệu quả của hệ thống**: Dựa trên các báo cáo, tường lửa, IDS (Suricata), và các dịch vụ khác có hoạt động hiệu quả không? Các cảnh báo có được xử lý không?
4.  **Kiến nghị chiến lược cho giai đoạn tiếp theo**: Đưa ra các đề xuất mang tính chiến lược hơn. Để các kiến nghị có tính cập nhật và phù hợp, **hãy sử dụng Google Search để tham khảo các phương pháp bảo mật tốt nhất (best practices) và các hướng dẫn cấu hình an toàn cho pfSense được công bố gần đây** từ các nguồn uy tín (ví dụ: CISA, SANS, Netgate blog).

Lưu ý:
- **Không lặp lại chi tiết** từng báo cáo con. Hãy tập trung vào việc **tổng hợp và nhận định xu hướng**.
- Giữ văn phong chuyên nghiệp, báo cáo cho cấp quản lý.
- Năm hiện tại là 2025.

--- DỮ LIỆU TỔNG HỢP TỪ CÁC BÁO CÁO ---
{reports_content}
--- KẾT THÚC DỮ LIỆU TỔNG HỢP ---