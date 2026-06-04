# Auth-Gated App Testing Playbook (Google Auth)

## Step 1: Create Test User & Session
```
mongosh --eval "
use('test_database');
var userId = 'user_testadmin01';
var sessionToken = 'test_session_admin_persist';
db.users.updateOne({user_id:userId},{\$set:{
  user_id: userId,
  email: 'admin@flowdesk.test',
  name: 'Test Admin',
  role: 'admin',
  picture: 'https://via.placeholder.com/150',
  active: true,
  created_at: new Date()
}},{upsert:true});
db.user_sessions.insertOne({
  user_id: userId,
  session_token: sessionToken,
  expires_at: new Date(Date.now() + 7*24*60*60*1000),
  created_at: new Date()
});
print('Session token: ' + sessionToken);
"
```

## Step 2: Test Backend API
```
curl -X GET "$URL/api/auth/me" -H "Authorization: Bearer test_session_admin_persist"
curl -X GET "$URL/api/dashboard/metrics" -H "Authorization: Bearer test_session_admin_persist"
curl -X GET "$URL/api/leads" -H "Authorization: Bearer test_session_admin_persist"
```

## Step 3: Browser Testing
```
await page.context.add_cookies([{
    "name": "session_token",
    "value": "test_session_admin_persist",
    "domain": "<host-without-https>",
    "path": "/",
    "httpOnly": True,
    "secure": True,
    "sameSite": "None"
}])
await page.goto("<app-url>/dashboard")
```

## Notes
- Users have a `role` field: admin | supervisor | sales_agent.
- All queries exclude MongoDB `_id` with `{"_id": 0}` projection.
- Session token sent via httpOnly cookie OR Authorization: Bearer header.
