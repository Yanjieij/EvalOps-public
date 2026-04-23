// Package handler holds Gin HTTP handlers.
//
// Files in this package should remain thin: take the Gin context, parse
// the request, call a scheduler/store service, and respond. Business
// logic lives in scheduler/ and store/, not here.
package handler
